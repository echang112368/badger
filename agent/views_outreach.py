from __future__ import annotations

import json
from typing import Any

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from creators.models import CreatorMeta
from merchants.models import MerchantMeta

from .models import OutreachAgentInteraction, OutreachDraft, OutreachThreadSummary
from .services import gmail as gmail_service
from .services.outreach_agents import (
    VALID_TONES,
    OutreachAgentOutput,
    run_email_writer,
    run_reply_suggestion,
    run_thread_summary,
)


def _json_body(request) -> dict[str, Any]:
    try:
        return json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _error(message: str, status: int = 400, code: str = "error") -> JsonResponse:
    return JsonResponse({"ok": False, "error": {"code": code, "message": message}}, status=status)


def _output_dict(output: OutreachAgentOutput) -> dict[str, Any]:
    return output.model_dump() if hasattr(output, "model_dump") else output.dict()


def _valid_email_or_error(value: str) -> str:
    email = (value or "").strip()
    validate_email(email)
    return email


def _valid_tone(value: str) -> str:
    tone = (value or "professional").strip().lower()
    return tone if tone in VALID_TONES else "professional"


def _business_for_user(business_id: Any):
    if not business_id:
        return None
    return get_object_or_404(MerchantMeta.objects.select_related("user"), user_id=business_id)


def _business_context(meta: MerchantMeta | None) -> dict[str, Any]:
    if not meta:
        return {}
    return {
        "id": meta.user_id,
        "company_name": meta.company_name or meta.user.get_full_name() or meta.user.username,
        "marketplace_enabled": meta.marketplace_enabled,
        "business_type": meta.business_type,
        "shopify_store_domain": meta.shopify_store_domain,
        "billing_plan": meta.billing_plan,
    }


def _creator_context(user) -> dict[str, Any]:
    meta, _ = CreatorMeta.objects.get_or_create(user=user)
    return {
        "username": user.username,
        "first_name": user.first_name,
        "bio": meta.bio,
        "short_pitch": meta.short_pitch,
        "social_media_platform": meta.social_media_platform,
        "follower_range": meta.follower_range,
        "social_media_profiles": meta.social_media_profiles,
        "content_skills": meta.content_skills,
        "niches": meta.niches,
        "country": meta.country,
        "content_languages": meta.content_languages,
        "paid_brand_deals_count": meta.paid_brand_deals_count,
        "gifted_brand_deals_count": meta.gifted_brand_deals_count,
        "affiliate_brand_deals_count": meta.affiliate_brand_deals_count,
        "avg_sponsored_conversion_rate_pct": meta.avg_sponsored_conversion_rate_pct,
        "partnership_history_notes": meta.partnership_history_notes,
    }


def _safe_business(meta: MerchantMeta) -> dict[str, Any]:
    return {
        "id": meta.user_id,
        "company_name": meta.company_name or meta.user.get_full_name() or meta.user.username,
        "contact_email": meta.user.email or "",
        "marketplace_enabled": meta.marketplace_enabled,
        "business_type": meta.business_type,
        "shopify_store_domain": meta.shopify_store_domain,
    }


def _save_interaction(user, action_type: str, business, safe_input: dict, output: dict, error: str = "") -> None:
    OutreachAgentInteraction.objects.create(
        creator=user,
        business=business.user if business else None,
        action_type=action_type,
        safe_input=safe_input,
        structured_output=output,
        error_message=error,
    )


def _gmail_error(exc: Exception) -> JsonResponse:
    if isinstance(exc, gmail_service.MissingGmailConnection):
        return _error("Connect Gmail before using this action.", status=409, code="gmail_missing")
    if isinstance(exc, (gmail_service.GmailNeedsReauth, gmail_service.GmailPermissionError)):
        return _error("Reconnect Gmail before using this action.", status=409, code="gmail_reauth")
    return _error("Gmail could not complete that action. Please try again.", status=502, code="gmail_api_error")


@login_required
@require_GET
def outreach_business_search(request):
    query = (request.GET.get("q") or "").strip()
    metas = MerchantMeta.objects.select_related("user").filter(user__is_merchant=True)
    if query:
        metas = metas.filter(Q(company_name__icontains=query) | Q(user__username__icontains=query) | Q(user__email__icontains=query) | Q(shopify_store_domain__icontains=query))
    metas = metas.order_by("company_name", "user__username")[:12]
    return JsonResponse({"ok": True, "businesses": [_safe_business(meta) for meta in metas]})


@login_required
@require_POST
def outreach_generate(request):
    payload = _json_body(request)
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email", ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    business = _business_for_user(payload.get("business_id")) if payload.get("business_id") else None
    manual_business = (payload.get("manual_business") or "").strip()
    if not business and not manual_business:
        return _error("Select a business or enter manual business context.", code="missing_business")
    tone = _valid_tone(payload.get("tone"))
    context = {
        "action": "generate",
        "creator_profile": _creator_context(request.user),
        "business": _business_context(business) or {"manual_context": manual_business},
        "recipient_email": recipient,
        "tone": tone,
        "partnership_context": (payload.get("partnership_context") or "").strip(),
    }
    output = run_email_writer(context, recipient)
    output_data = _output_dict(output)
    draft = OutreachDraft.objects.create(
        creator=request.user,
        business=business.user if business else None,
        recipient_email=recipient,
        subject=output.email.subject,
        body=output.email.body,
        tone=tone,
        status=OutreachDraft.STATUS_GENERATED,
        last_agent_response=output_data,
    )
    _save_interaction(request.user, OutreachAgentInteraction.ACTION_GENERATE, business, {"tone": tone, "has_business": bool(business)}, output_data)
    return JsonResponse({"ok": True, "draft_id": draft.id, "output": output_data})


@login_required
@require_POST
def outreach_revise(request):
    payload = _json_body(request)
    draft = None
    if payload.get("draft_id"):
        draft = get_object_or_404(OutreachDraft, id=payload.get("draft_id"), creator=request.user)
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email") or (draft.recipient_email if draft else ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    tone = _valid_tone(payload.get("tone") or (draft.tone if draft else "professional"))
    revision_instruction = (payload.get("revision_instruction") or "").strip()
    if not revision_instruction and not payload.get("tone"):
        return _error("Enter a revision instruction or choose a tone.", code="missing_revision")
    business_meta = None
    if draft and draft.business_id:
        business_meta = MerchantMeta.objects.filter(user_id=draft.business_id).select_related("user").first()
    context = {
        "action": "revise",
        "creator_profile": _creator_context(request.user),
        "business": _business_context(business_meta),
        "recipient_email": recipient,
        "tone": tone,
        "revision_instruction": revision_instruction,
        "existing_draft": {
            "subject": payload.get("subject") or (draft.subject if draft else ""),
            "body": payload.get("body") or (draft.body if draft else ""),
        },
    }
    output = run_email_writer(context, recipient)
    output_data = _output_dict(output)
    if draft:
        draft.recipient_email = recipient
        draft.subject = output.email.subject
        draft.body = output.email.body
        draft.tone = tone
        draft.status = OutreachDraft.STATUS_EDITED
        draft.last_agent_response = output_data
        draft.save(update_fields=["recipient_email", "subject", "body", "tone", "status", "last_agent_response", "updated_at"])
    _save_interaction(request.user, OutreachAgentInteraction.ACTION_REVISE, business_meta, {"tone": tone}, output_data)
    return JsonResponse({"ok": True, "draft_id": draft.id if draft else None, "output": output_data})


@login_required
@require_POST
def outreach_search_threads(request):
    payload = _json_body(request)
    query = (payload.get("query") or "").strip()
    if not query:
        return _error("Enter a Gmail search query.", code="missing_query")
    try:
        threads = gmail_service.search_threads(request.user, query, payload.get("max_results", 10))
    except Exception as exc:
        return _gmail_error(exc)
    return JsonResponse({"ok": True, "threads": threads})


@login_required
@require_POST
def outreach_read_thread(request):
    thread_id = (_json_body(request).get("thread_id") or "").strip()
    if not thread_id:
        return _error("Choose a Gmail thread.", code="missing_thread")
    try:
        thread = gmail_service.read_thread(request.user, thread_id)
    except Exception as exc:
        return _gmail_error(exc)
    return JsonResponse({"ok": True, "thread": thread})


def _thread_from_payload_or_gmail(request, payload):
    thread = payload.get("thread") if isinstance(payload.get("thread"), dict) else None
    thread_id = (payload.get("thread_id") or (thread or {}).get("id") or "").strip()
    if thread:
        return thread_id, thread
    if not thread_id:
        raise ValueError("Choose a Gmail thread.")
    return thread_id, gmail_service.read_thread(request.user, thread_id)


@login_required
@require_POST
def outreach_summarize_thread(request):
    payload = _json_body(request)
    try:
        thread_id, thread = _thread_from_payload_or_gmail(request, payload)
    except ValueError as exc:
        return _error(str(exc), code="missing_thread")
    except Exception as exc:
        return _gmail_error(exc)
    output = run_thread_summary({"thread_id": thread_id, "thread": thread})
    output_data = _output_dict(output)
    OutreachThreadSummary.objects.update_or_create(
        creator=request.user,
        gmail_thread_id=thread_id,
        defaults={"summary": output.summary, "next_actions": output_data.get("items", [])},
    )
    _save_interaction(request.user, OutreachAgentInteraction.ACTION_SUMMARIZE_THREAD, None, {"thread_id": thread_id}, output_data)
    return JsonResponse({"ok": True, "output": output_data})


@login_required
@require_POST
def outreach_next_actions(request):
    payload = _json_body(request)
    try:
        thread_id, thread = _thread_from_payload_or_gmail(request, payload)
    except ValueError as exc:
        return _error(str(exc), code="missing_thread")
    except Exception as exc:
        return _gmail_error(exc)
    output = run_thread_summary({"thread_id": thread_id, "thread": thread}, next_actions_only=True)
    output_data = _output_dict(output)
    _save_interaction(request.user, OutreachAgentInteraction.ACTION_NEXT_ACTIONS, None, {"thread_id": thread_id}, output_data)
    return JsonResponse({"ok": True, "output": output_data})


@login_required
@require_POST
def outreach_suggest_reply(request):
    payload = _json_body(request)
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email", ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    try:
        thread_id, thread = _thread_from_payload_or_gmail(request, payload)
    except ValueError as exc:
        return _error(str(exc), code="missing_thread")
    except Exception as exc:
        return _gmail_error(exc)
    tone = _valid_tone(payload.get("tone"))
    output = run_reply_suggestion({"creator_profile": _creator_context(request.user), "thread": thread, "thread_id": thread_id, "tone": tone, "recipient_email": recipient}, recipient)
    output_data = _output_dict(output)
    _save_interaction(request.user, OutreachAgentInteraction.ACTION_SUGGEST_REPLY, None, {"thread_id": thread_id, "tone": tone}, output_data)
    return JsonResponse({"ok": True, "output": output_data})


@login_required
@require_POST
def outreach_save_draft(request):
    payload = _json_body(request)
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email", ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject or not body:
        return _error("Subject and body are required before saving a Gmail draft.", code="missing_content")
    draft = None
    if payload.get("draft_id"):
        draft = get_object_or_404(OutreachDraft, id=payload.get("draft_id"), creator=request.user)
    try:
        result = gmail_service.create_draft(request.user, recipient, subject, body, payload.get("thread_id") or None, payload.get("in_reply_to_message_id") or None)
    except Exception as exc:
        return _gmail_error(exc)
    if not draft:
        draft = OutreachDraft.objects.create(creator=request.user, recipient_email=recipient)
    draft.recipient_email = recipient
    draft.subject = subject
    draft.body = body
    draft.tone = _valid_tone(payload.get("tone") or draft.tone)
    draft.gmail_draft_id = result.get("draft_id", "")
    draft.gmail_message_id = result.get("message_id", "")
    draft.gmail_thread_id = result.get("thread_id", payload.get("thread_id", ""))
    draft.status = OutreachDraft.STATUS_GMAIL_DRAFTED
    draft.save()
    return JsonResponse({"ok": True, "draft_id": draft.id, "gmail": result})


@login_required
@require_POST
def outreach_update_draft(request):
    payload = _json_body(request)
    draft = get_object_or_404(OutreachDraft, id=payload.get("draft_id"), creator=request.user)
    if not draft.gmail_draft_id:
        return _error("This local draft is not linked to a Gmail draft yet.", code="missing_gmail_draft")
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email", draft.recipient_email))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    subject = (payload.get("subject") or draft.subject).strip()
    body = (payload.get("body") or draft.body).strip()
    if not subject or not body:
        return _error("Subject and body are required before updating a Gmail draft.", code="missing_content")
    try:
        result = gmail_service.update_draft(request.user, draft.gmail_draft_id, recipient, subject, body)
    except Exception as exc:
        return _gmail_error(exc)
    draft.recipient_email = recipient
    draft.subject = subject
    draft.body = body
    draft.gmail_message_id = result.get("message_id", draft.gmail_message_id)
    draft.gmail_thread_id = result.get("thread_id", draft.gmail_thread_id)
    draft.status = OutreachDraft.STATUS_GMAIL_DRAFTED
    draft.save()
    return JsonResponse({"ok": True, "draft_id": draft.id, "gmail": result})


@login_required
@require_POST
def outreach_send(request):
    payload = _json_body(request)
    if payload.get("confirm") is not True:
        return _error("Sending requires explicit confirmation.", code="confirm_required")
    draft = None
    if payload.get("draft_id"):
        draft = get_object_or_404(OutreachDraft, id=payload.get("draft_id"), creator=request.user)
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email") or (draft.recipient_email if draft else ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    subject = (payload.get("subject") or (draft.subject if draft else "")).strip()
    body = (payload.get("body") or (draft.body if draft else "")).strip()
    if not subject or not body:
        return _error("Subject and body are required before sending.", code="missing_content")
    try:
        if draft and draft.gmail_draft_id:
            result = gmail_service.send_draft(request.user, draft.gmail_draft_id)
        else:
            result = gmail_service.send_email(request.user, recipient, subject, body)
    except Exception as exc:
        if draft:
            draft.status = OutreachDraft.STATUS_FAILED
            draft.save(update_fields=["status", "updated_at"])
        return _gmail_error(exc)
    if draft:
        draft.recipient_email = recipient
        draft.subject = subject
        draft.body = body
        draft.gmail_message_id = result.get("message_id", draft.gmail_message_id)
        draft.gmail_thread_id = result.get("thread_id", draft.gmail_thread_id)
        draft.status = OutreachDraft.STATUS_SENT
        draft.sent_at = timezone.now()
        draft.save()
    return JsonResponse({"ok": True, "draft_id": draft.id if draft else None, "gmail": result})


@login_required
@require_POST
def outreach_reply(request):
    payload = _json_body(request)
    thread_id = (payload.get("thread_id") or "").strip()
    if not thread_id:
        return _error("Choose a Gmail thread.", code="missing_thread")
    try:
        recipient = _valid_email_or_error(payload.get("recipient_email", ""))
    except ValidationError:
        return _error("Enter a valid recipient email.", code="invalid_email")
    subject = (payload.get("subject") or "").strip()
    body = (payload.get("body") or "").strip()
    if not subject or not body:
        return _error("Subject and body are required.", code="missing_content")
    if payload.get("confirm") is True:
        try:
            result = gmail_service.reply_to_thread(request.user, thread_id, recipient, subject, body)
        except Exception as exc:
            return _gmail_error(exc)
        draft = OutreachDraft.objects.create(
            creator=request.user,
            recipient_email=recipient,
            subject=subject,
            body=body,
            gmail_thread_id=result.get("thread_id", thread_id),
            gmail_message_id=result.get("message_id", ""),
            status=OutreachDraft.STATUS_SENT,
            sent_at=timezone.now(),
        )
        return JsonResponse({"ok": True, "draft_id": draft.id, "gmail": result})
    try:
        result = gmail_service.create_draft(request.user, recipient, subject, body, thread_id)
    except Exception as exc:
        return _gmail_error(exc)
    draft = OutreachDraft.objects.create(
        creator=request.user,
        recipient_email=recipient,
        subject=subject,
        body=body,
        gmail_thread_id=result.get("thread_id", thread_id),
        gmail_message_id=result.get("message_id", ""),
        gmail_draft_id=result.get("draft_id", ""),
        status=OutreachDraft.STATUS_GMAIL_DRAFTED,
    )
    return JsonResponse({"ok": True, "draft_id": draft.id, "gmail": result})
