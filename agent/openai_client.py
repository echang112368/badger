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
from .prompts import CONTRACT_REVIEW_SYSTEM_PROMPT, CREATOR_AGENT_SYSTEM_PROMPT
from .services.outreach_agents import run_email_writer

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_CREATOR_AGENT_MODEL = "gpt-4.1-mini"
logger = logging.getLogger(__name__)

CONTRACT_REVIEW_MAX_ATTACHMENTS = 4
CONTRACT_REVIEW_MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
CONTRACT_REVIEW_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


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



def _extract_rate_calculator_command(message: str) -> str | None:
    stripped = message.strip()
    command, separator, prompt_text = stripped.partition(" ")
    if command.lower() not in {"/rate-calculator", "/calculate-rate"}:
        return None
    return prompt_text.strip() if separator else ""


def _rate_calculator_command_reply(message: str) -> str | None:
    prompt_text = _extract_rate_calculator_command(message)
    if prompt_text is None:
        return None

    extra_context = f"\n\nI captured your notes for the estimate: {prompt_text}" if prompt_text else ""
    return (
        "### Rate Calculator\n"
        "Use the structured Rate Calculator to price campaign deliverables from average views, engagement, "
        "audience quality, niche, usage rights, whitelisting, exclusivity, production complexity, rush timing, "
        "and bundle scope. It opens as a normal page instead of an iframe so the browser does not block it.\n\n"
        "[Open the Rate Calculator](/rate-calculator/)"
        f"{extra_context}\n\n"
        "Creator rates are not standardized; this is a data-backed estimate, not a guaranteed market price. "
        "This is not legal advice."
    )


def _extract_contract_review_prompt(message: str) -> str | None:
    stripped = message.strip()
    command, separator, prompt_text = stripped.partition(" ")
    if command.lower() != "/contract-review":
        return None
    return prompt_text.strip() if separator else ""


def _contract_review_usage_message() -> str:
    return (
        "Use `/contract-review` with the contract you want me to analyze. "
        "You can paste the agreement text after the command, attach a PDF or text file, "
        "or attach a screenshot/image of the contract. "
        "Example: `/contract-review Deliverables: 2 TikToks by July 15...`"
    )


def _normalize_contract_review_attachments(attachments: list[dict[str, Any]] | None) -> list[dict[str, str]]:
    normalized = []
    for raw in attachments or []:
        if not isinstance(raw, dict):
            continue
        filename = str(raw.get("filename") or raw.get("name") or "contract-attachment").strip()
        mime_type = str(raw.get("mime_type") or raw.get("content_type") or "application/octet-stream").strip()
        data_url = str(raw.get("data_url") or "").strip()
        base64_data = str(raw.get("content_base64") or raw.get("data") or "").strip()

        if not data_url and base64_data:
            data_url = f"data:{mime_type};base64,{base64_data}"
        if not data_url.startswith("data:"):
            continue
        try:
            encoded = data_url.split(",", 1)[1]
        except IndexError:
            continue
        estimated_size = (len(encoded) * 3) // 4
        if estimated_size > CONTRACT_REVIEW_MAX_ATTACHMENT_BYTES:
            continue
        normalized.append({"filename": filename, "mime_type": mime_type, "data_url": data_url})
        if len(normalized) >= CONTRACT_REVIEW_MAX_ATTACHMENTS:
            break
    return normalized


def _contract_review_attachment_content_items(attachments: list[dict[str, str]]) -> list[dict[str, Any]]:
    content_items = []
    for attachment in attachments:
        filename = attachment["filename"]
        mime_type = attachment["mime_type"]
        data_url = attachment["data_url"]
        if mime_type in CONTRACT_REVIEW_IMAGE_MIME_TYPES:
            content_items.append(
                {
                    "type": "input_image",
                    "image_url": data_url,
                    "detail": "high",
                }
            )
        else:
            content_items.append(
                {
                    "type": "input_file",
                    "filename": filename,
                    "file_data": data_url,
                }
            )
    return content_items


def build_contract_review_input(user, contract_text: str) -> str:
    creator_context = {
        "creator_profile": _creator_profile_summary(user),
        "connected_accounts": _connected_accounts_summary(user),
    }
    return (
        f"{CONTRACT_REVIEW_SYSTEM_PROMPT}\n\n"
        "Use the creator context only to make examples or platform notes more relevant; do not invent contract facts from it.\n"
        f"Creator context JSON: {json.dumps(creator_context, default=str, ensure_ascii=False)}\n\n"
        "Pasted contract text, if any, starts below. Analyze only the provided text and attached contract materials; cite exact supplied or visible clause language when flagging concerns.\n\n"
        f"{contract_text}"
    )


def build_contract_review_request_input(
    user, contract_text: str, attachments: list[dict[str, Any]] | None = None
) -> str | list[dict[str, Any]]:
    normalized_attachments = _normalize_contract_review_attachments(attachments)
    text_input = build_contract_review_input(user, contract_text)
    if not normalized_attachments:
        return text_input

    content_items = [{"type": "input_text", "text": text_input}]
    content_items.extend(_contract_review_attachment_content_items(normalized_attachments))
    attachment_names = ", ".join(item["filename"] for item in normalized_attachments)
    content_items.append(
        {
            "type": "input_text",
            "text": (
                "Attached contract materials: "
                f"{attachment_names}. Review the pasted text and attached file/image content together. "
                "If text is visible in a screenshot, read the screenshot and reference the visible clause language."
            ),
        }
    )
    return [{"role": "user", "content": content_items}]


def _maybe_contract_review_command_reply(
    user, message: str, attachments: list[dict[str, Any]] | None = None, timeout_seconds: int | None = None
) -> str | None:
    contract_text = _extract_contract_review_prompt(message)
    if contract_text is None:
        return None
    normalized_attachments = _normalize_contract_review_attachments(attachments)
    if not contract_text and not normalized_attachments:
        return _contract_review_usage_message()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning("/contract-review skipped: OPENAI_API_KEY is missing.")
        return (
            "I can run `/contract-review`, but the OpenAI API key is not configured on the server. "
            "Set OPENAI_API_KEY to enable contract reviews."
        )

    model = os.environ.get(
        "OPENAI_CREATOR_AGENT_MODEL",
        getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_CREATOR_AGENT_MODEL),
    ).strip() or DEFAULT_CREATOR_AGENT_MODEL
    if timeout_seconds is None:
        timeout_seconds = int(os.environ.get("OPENAI_CONTRACT_REVIEW_TIMEOUT", "45"))

    body = {
        "model": model,
        "input": build_contract_review_request_input(user, contract_text, normalized_attachments),
        "temperature": 0.2,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    try:
        response = requests.post(OPENAI_RESPONSES_URL, headers=headers, json=body, timeout=timeout_seconds)
        response.raise_for_status()
        response_payload = response.json()
        output_text = _extract_output_text(response_payload)
    except requests.Timeout as exc:
        logger.exception("/contract-review OpenAI request timed out for user_id=%s", user.id)
        return f"OpenAI API timeout while reviewing the contract: {exc}"
    except requests.RequestException as exc:
        logger.exception("/contract-review OpenAI request failed for user_id=%s", user.id)
        return f"OpenAI API request failed while reviewing the contract: {exc}"
    except (ValueError, TypeError) as exc:
        logger.exception("/contract-review OpenAI response parsing failed for user_id=%s", user.id)
        return f"OpenAI response parsing failed while reviewing the contract: {exc}"

    if output_text:
        return output_text

    logger.warning("/contract-review OpenAI response returned no output text for user_id=%s", user.id)
    return "OpenAI returned an empty contract review. Please try again with the contract text."


def _stream_contract_review_command_reply(
    user, message: str, attachments: list[dict[str, Any]] | None = None, timeout_seconds: int | None = None
) -> Generator[str, None, None] | None:
    contract_text = _extract_contract_review_prompt(message)
    if contract_text is None:
        return None
    normalized_attachments = _normalize_contract_review_attachments(attachments)

    def _stream() -> Generator[str, None, None]:
        if not contract_text and not normalized_attachments:
            yield _contract_review_usage_message()
            return

        api_key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not api_key:
            logger.warning("/contract-review streaming skipped: OPENAI_API_KEY is missing.")
            yield (
                "I can run `/contract-review`, but the OpenAI API key is not configured on the server. "
                "Set OPENAI_API_KEY to enable contract reviews."
            )
            return

        model = os.environ.get(
            "OPENAI_CREATOR_AGENT_MODEL",
            getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_CREATOR_AGENT_MODEL),
        ).strip() or DEFAULT_CREATOR_AGENT_MODEL
        actual_timeout = timeout_seconds
        if actual_timeout is None:
            actual_timeout = int(os.environ.get("OPENAI_CONTRACT_REVIEW_TIMEOUT", "90"))

        body = {
            "model": model,
            "input": build_contract_review_request_input(user, contract_text, normalized_attachments),
            "temperature": 0.2,
            "stream": True,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        try:
            with requests.post(
                OPENAI_RESPONSES_URL, headers=headers, json=body, stream=True, timeout=actual_timeout
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
            logger.exception("/contract-review streaming timed out for user_id=%s", user.id)
            yield "\n\n[Contract review timed out. Please try again.]"
        except requests.RequestException as exc:
            logger.exception("/contract-review streaming failed for user_id=%s", user.id)
            yield f"\n\n[Error connecting to AI service for contract review: {exc}]"

    return _stream()


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


def generate_creator_agent_reply(
    user, conversation, message: str, attachments: list[dict[str, Any]] | None = None, timeout_seconds: int | None = None
) -> str:
    rate_calculator_reply = _rate_calculator_command_reply(message)
    if rate_calculator_reply is not None:
        return rate_calculator_reply

    contract_review_reply = _maybe_contract_review_command_reply(
        user, message, attachments=attachments, timeout_seconds=timeout_seconds
    )
    if contract_review_reply is not None:
        return contract_review_reply

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
    user, conversation, message: str, attachments: list[dict[str, Any]] | None = None, timeout_seconds: int | None = None
) -> Generator[str, None, None]:
    """Yields text delta chunks as they stream from the OpenAI Responses API."""
    rate_calculator_reply = _rate_calculator_command_reply(message)
    if rate_calculator_reply is not None:
        yield rate_calculator_reply
        return

    contract_review_stream = _stream_contract_review_command_reply(
        user, message, attachments=attachments, timeout_seconds=timeout_seconds
    )
    if contract_review_stream is not None:
        yield from contract_review_stream
        return

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
