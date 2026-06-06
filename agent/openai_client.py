from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Generator

import requests
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

from creators.models import CreatorMeta
from creators.services.dashboard import build_creator_dashboard_context
from creators.services.gmail_oauth import get_gmail_connection_status
from instagram_connect.models import InstagramConnection

from .models import OutreachDraft
from .services import gmail as gmail_service
from .prompts import CREATOR_AGENT_SYSTEM_PROMPT
from .services.outreach_agents import run_email_writer

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_CREATOR_AGENT_MODEL = "gpt-4.1-mini"
logger = logging.getLogger(__name__)


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip()

    output = payload.get("output") or []
    for item in output:
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    return ""


def _connected_accounts_summary(user) -> list[dict[str, Any]]:
    accounts = []
    for connection in InstagramConnection.objects.filter(user=user):
        accounts.append(
            {
                "platform": "Instagram",
                "username": connection.instagram_username or "connected",
                "followers_count": connection.followers_count,
                "media_count": connection.media_count,
                "last_synced_at": connection.last_synced_at.isoformat() if connection.last_synced_at else None,
            }
        )
    return accounts


def _creator_profile_summary(user) -> dict[str, Any]:
    creator_meta, _ = CreatorMeta.objects.get_or_create(user=user)
    return {
        "username": user.username,
        "first_name": user.first_name,
        "bio": creator_meta.bio,
        "short_pitch": creator_meta.short_pitch,
        "primary_platform": creator_meta.social_media_platform,
        "follower_range": creator_meta.follower_range,
        "social_profiles": creator_meta.social_media_profiles,
        "content_skills": creator_meta.content_skills,
        "niches": creator_meta.niches,
        "country": creator_meta.country,
        "content_languages": creator_meta.content_languages,
        "marketplace_enabled": creator_meta.marketplace_enabled,
        "paid_brand_deals_count": creator_meta.paid_brand_deals_count,
        "gifted_brand_deals_count": creator_meta.gifted_brand_deals_count,
        "affiliate_brand_deals_count": creator_meta.affiliate_brand_deals_count,
        "avg_sponsored_conversion_rate_pct": creator_meta.avg_sponsored_conversion_rate_pct,
        "partnership_history_notes": creator_meta.partnership_history_notes,
    }


def _recent_history(conversation, limit: int = 12) -> list[dict[str, str]]:
    messages = conversation.messages.order_by("-created_at")[:limit]
    return [
        {"role": message.role, "content": message.content}
        for message in reversed(list(messages))
    ]


def _gmail_summary(user) -> dict[str, Any]:
    try:
        status = get_gmail_connection_status(user)
        return {"connected": status["connected"], "email": status.get("gmail_email", "")}
    except Exception:
        return {"connected": False, "email": ""}


def _output_to_dict(output) -> dict[str, Any]:
    return output.model_dump() if hasattr(output, "model_dump") else output.dict()


def _valid_recipient_from_text(text: str) -> str | None:
    recipient_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, flags=re.IGNORECASE)
    if not recipient_match:
        return None
    recipient = recipient_match.group(0)
    try:
        validate_email(recipient)
    except ValidationError:
        return None
    return recipient


def _gmail_error_message(exc: Exception) -> str:
    if isinstance(exc, gmail_service.MissingGmailConnection):
        return "Connect Gmail before using `/gmail` to create drafts."
    if isinstance(exc, (gmail_service.GmailNeedsReauth, gmail_service.GmailPermissionError)):
        return "Reconnect Gmail before using `/gmail` to create drafts."
    logger.exception("/gmail command failed while creating Gmail draft.")
    return "Gmail could not create that draft. Please try again."


def _maybe_gmail_command_reply(user, message: str) -> str | None:
    """Handle explicit `/gmail ...` chat commands by writing a Gmail draft.

    `/gmail` is intentionally draft-only. It uses the existing EmailWritingAgent
    to prepare structured content, then the Django Gmail service creates a draft
    in the connected Gmail account. Sending remains behind the existing explicit
    confirmation flow in the outreach UI.
    """
    stripped = message.strip()
    command, separator, prompt_text = stripped.partition(" ")
    if command.lower() != "/gmail":
        return None

    prompt = prompt_text.strip() if separator else ""
    if not prompt:
        return (
            "Use `/gmail` followed by the draft instructions and recipient email. "
            "Example: `/gmail write a professional partnership email to brand@example.com about a gifted outerwear collab`."
        )

    recipient = _valid_recipient_from_text(prompt)
    if not recipient:
        return "Please include a valid recipient email after `/gmail` so I can create the Gmail draft."

    context = {
        "action": "slash_gmail_create_draft",
        "creator_profile": _creator_profile_summary(user),
        "business": {"manual_context": prompt},
        "recipient_email": recipient,
        "tone": "professional",
        "partnership_context": prompt,
        "gmail_command": True,
    }
    output = run_email_writer(context, recipient)
    output_data = _output_to_dict(output)

    if output.type != "draft_email":
        return output.summary or "The email writing agent could not create a draft from that prompt."

    subject = (output.email.subject or "").strip()
    body = (output.email.body or "").strip()
    if not subject or not body:
        return "The email writing agent returned an incomplete draft. Please try again with more context."

    draft = OutreachDraft.objects.create(
        creator=user,
        recipient_email=recipient,
        subject=subject,
        body=body,
        tone="professional",
        status=OutreachDraft.STATUS_GENERATED,
        last_agent_response=output_data,
    )

    try:
        result = gmail_service.create_draft(user, recipient, subject, body)
    except Exception as exc:
        draft.status = OutreachDraft.STATUS_FAILED
        draft.save(update_fields=["status", "updated_at"])
        return _gmail_error_message(exc)

    draft.gmail_draft_id = result.get("draft_id", "")
    draft.gmail_message_id = result.get("message_id", "")
    draft.gmail_thread_id = result.get("thread_id", "")
    draft.status = OutreachDraft.STATUS_GMAIL_DRAFTED
    draft.save(update_fields=["gmail_draft_id", "gmail_message_id", "gmail_thread_id", "status", "updated_at"])

    return (
        "I used the email writing agent and created a Gmail draft in your connected Gmail account.\n\n"
        f"**Gmail draft:** {draft.gmail_draft_id or 'created'}\n\n"
        f"**To:** {recipient}\n\n"
        f"**Subject:** {subject}\n\n"
        f"{body}\n\n"
        "This is only a draft; it has not been sent. Review it in Gmail or the Outreach Email Tool before sending."
    )


def _maybe_outreach_email_reply(user, message: str) -> str | None:
    """Route explicit outreach-email asks to the specialist Agents SDK writer.

    This keeps outreach as an internal capability of the main Agent page. It has
    no Gmail write side effects; Gmail draft/save/send still happens only from
    explicit UI actions in the integrated outreach tool.
    """
    lowered = message.lower()
    if not any(term in lowered for term in ("outreach", "partnership email", "brand email", "draft email", "cold email")):
        return None
    recipient = _valid_recipient_from_text(message)
    if not recipient:
        return (
            "I can use the outreach email specialist for that. Please provide the validated recipient email "
            "or open the Outreach Email Tool in this Agent page to select a business, confirm the recipient, "
            "and generate a draft. I will not create a Gmail draft or send anything without your explicit action."
        )
    context = {
        "action": "main_agent_email_outreach",
        "creator_profile": _creator_profile_summary(user),
        "business": {"manual_context": message},
        "recipient_email": recipient,
        "tone": "professional",
        "partnership_context": message,
    }
    output = run_email_writer(context, recipient)
    data = _output_to_dict(output)
    if output.type == "draft_email":
        draft = OutreachDraft.objects.create(
            creator=user,
            recipient_email=recipient,
            subject=output.email.subject,
            body=output.email.body,
            tone="professional",
            status=OutreachDraft.STATUS_GENERATED,
            last_agent_response=data,
        )
        return (
            f"I used the outreach email specialist to prepare a local draft (ID {draft.id}). "
            "Review and edit it in the Outreach Email Tool before saving it to Gmail or sending.\n\n"
            f"**To:** {output.email.to}\n\n"
            f"**Subject:** {output.email.subject}\n\n"
            f"{output.email.body}\n\n"
            "This has not been sent and no Gmail draft was created."
        )
    return output.summary

def build_creator_agent_input(user, conversation, message: str) -> str:
    dashboard_context = build_creator_dashboard_context(user)
    agent_context = {
        "creator_profile": _creator_profile_summary(user),
        "dashboard": dashboard_context,
        "connected_accounts": _connected_accounts_summary(user),
        "gmail_connection": _gmail_summary(user),
        "recent_conversation": _recent_history(conversation),
        "current_message": message,
    }
    return (
        f"{CREATOR_AGENT_SYSTEM_PROMPT}\n\n"
        "Use this JSON context for the logged-in creator. Do not expose raw JSON unless asked.\n"
        f"{json.dumps(agent_context, default=str, ensure_ascii=False)}"
    )


def generate_creator_agent_reply(user, conversation, message: str, timeout_seconds: int | None = None) -> str:
    gmail_command_reply = _maybe_gmail_command_reply(user, message)
    if gmail_command_reply is not None:
        return gmail_command_reply

    outreach_reply = _maybe_outreach_email_reply(user, message)
    if outreach_reply is not None:
        return outreach_reply

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("Creator agent OpenAI request skipped: OPENAI_API_KEY is missing.")
        return (
            "I’m ready to help, but the OpenAI API key is not configured on the server. "
            "Set OPENAI_API_KEY to enable live creator-agent responses."
        )

    model = os.environ.get(
        "OPENAI_CREATOR_AGENT_MODEL",
        getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_CREATOR_AGENT_MODEL),
    ).strip() or DEFAULT_CREATOR_AGENT_MODEL
    if timeout_seconds is None:
        timeout_seconds = int(os.environ.get("OPENAI_CREATOR_AGENT_TIMEOUT", "20"))

    body = {
        "model": model,
        "input": build_creator_agent_input(user, conversation, message),
        "temperature": 0.4,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(OPENAI_RESPONSES_URL, headers=headers, json=body, timeout=timeout_seconds)
        response.raise_for_status()
        response_payload = response.json()
        output_text = _extract_output_text(response_payload)
    except requests.Timeout as exc:
        logger.exception("Creator agent OpenAI request timed out for user_id=%s", user.id)
        return f"OpenAI API timeout while generating your creator-agent reply: {exc}"
    except requests.RequestException as exc:
        logger.exception("Creator agent OpenAI request failed for user_id=%s", user.id)
        return f"OpenAI API request failed while generating your creator-agent reply: {exc}"
    except (ValueError, TypeError) as exc:
        logger.exception("Creator agent OpenAI response parsing failed for user_id=%s", user.id)
        return f"OpenAI response parsing failed while generating your creator-agent reply: {exc}"

    if output_text:
        return output_text

    logger.warning("Creator agent OpenAI response returned no output text for user_id=%s", user.id)
    return "OpenAI returned an empty response. Please try again."


def stream_creator_agent_reply(
    user, conversation, message: str, timeout_seconds: int | None = None
) -> Generator[str, None, None]:
    """Yields text delta chunks as they stream from the OpenAI Responses API."""
    gmail_command_reply = _maybe_gmail_command_reply(user, message)
    if gmail_command_reply is not None:
        yield gmail_command_reply
        return

    outreach_reply = _maybe_outreach_email_reply(user, message)
    if outreach_reply is not None:
        yield outreach_reply
        return

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("Creator agent streaming skipped: OPENAI_API_KEY is missing.")
        yield (
            "I'm ready to help, but the OpenAI API key is not configured on the server. "
            "Set OPENAI_API_KEY to enable live creator-agent responses."
        )
        return

    model = os.environ.get(
        "OPENAI_CREATOR_AGENT_MODEL",
        getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_CREATOR_AGENT_MODEL),
    ).strip() or DEFAULT_CREATOR_AGENT_MODEL
    if timeout_seconds is None:
        timeout_seconds = int(os.environ.get("OPENAI_CREATOR_AGENT_TIMEOUT", "60"))

    body = {
        "model": model,
        "input": build_creator_agent_input(user, conversation, message),
        "temperature": 0.4,
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        with requests.post(
            OPENAI_RESPONSES_URL, headers=headers, json=body, stream=True, timeout=timeout_seconds
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line:
                    continue
                line_str = line.decode("utf-8") if isinstance(line, bytes) else line
                if not line_str.startswith("data: "):
                    continue
                data_str = line_str[6:]
                if data_str == "[DONE]":
                    break
                try:
                    event_data = json.loads(data_str)
                except (json.JSONDecodeError, ValueError):
                    continue
                if event_data.get("type") == "response.output_text.delta":
                    delta = event_data.get("delta", "")
                    if delta:
                        yield delta
    except requests.Timeout:
        logger.exception("Creator agent streaming timed out for user_id=%s", user.id)
        yield "\n\n[Response timed out. Please try again.]"
    except requests.RequestException as exc:
        logger.exception("Creator agent streaming failed for user_id=%s", user.id)
        yield f"\n\n[Error connecting to AI service: {exc}]"
