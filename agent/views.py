import json
import re

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from creators.models import CreatorMeta, OutreachDraft, OutreachAgentInteraction
from creators.services.gmail_oauth import get_gmail_connection_status
from .openai_client import generate_creator_agent_reply, stream_creator_agent_reply



OUTREACH_INTENT_RE = re.compile(
    r"\b(outreach|gmail|email|draft|revise|subject line|follow[- ]?up|reply|thread|inbox)\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.IGNORECASE)


def _looks_like_outreach_request(content: str) -> bool:
    return bool(OUTREACH_INTENT_RE.search(content or ""))


def _extract_email(content: str) -> str:
    match = EMAIL_RE.search(content or "")
    return match.group(0) if match else ""


def _creator_outreach_context(user) -> dict:
    meta, _ = CreatorMeta.objects.get_or_create(user=user)
    return {
        "username": user.username,
        "first_name": user.first_name,
        "email": user.email,
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


def _agent_output_dict(output) -> dict:
    if hasattr(output, "model_dump"):
        return output.model_dump()
    if isinstance(output, dict):
        return output
    return {
        "type": "error",
        "summary": "The email outreach specialist could not return structured output.",
        "email": {"to": "", "subject": "", "body": ""},
        "items": [],
        "requires_user_approval": False,
        "followup": "Try again with a recipient email or use the embedded outreach tool.",
    }


def _run_main_agent_outreach_tool(user, content: str) -> dict:
    recipient = _extract_email(content)
    payload = {
        "creator_profile": _creator_outreach_context(user),
        "business_profile": {},
        "recipient_email": recipient,
        "tone": "professional",
        "partnership_context": content,
    }
    try:
        from agent.services.outreach_agents import generate_outreach_email

        output = generate_outreach_email(payload)
        output_dict = _agent_output_dict(output)
    except Exception as exc:
        output_dict = {
            "type": "error",
            "summary": "The email outreach specialist is unavailable right now.",
            "email": {"to": recipient, "subject": "", "body": ""},
            "items": [],
            "requires_user_approval": False,
            "followup": str(exc)[:240],
        }

    draft_id = None
    email_payload = output_dict.get("email") or {}
    if output_dict.get("type") == "draft_email":
        email_payload["to"] = recipient or email_payload.get("to", "")
        output_dict["requires_user_approval"] = True
        try:
            validate_email(email_payload.get("to", ""))
        except ValidationError:
            output_dict = {
                "type": "error",
                "summary": "I can help write the outreach email, but I need a valid recipient email before creating a draft.",
                "email": {"to": recipient, "subject": email_payload.get("subject", ""), "body": email_payload.get("body", "")},
                "items": [],
                "requires_user_approval": False,
                "followup": "Add a recipient email in the outreach tool below, then click Generate outreach email.",
            }
        else:
            draft = OutreachDraft.objects.create(
                creator=user,
                recipient_email=email_payload.get("to", ""),
                subject=(email_payload.get("subject") or "")[:255],
                body=email_payload.get("body") or "",
                tone="professional",
                last_agent_response=output_dict,
            )
            draft_id = draft.id
    OutreachAgentInteraction.objects.create(
        creator=user,
        action_type=OutreachAgentInteraction.ACTION_GENERATE,
        safe_input={"source": "main_agent_chat", "recipient_email_present": bool(recipient)},
        structured_output=output_dict,
    )
    return {
        "agent": output_dict,
        "draft_id": draft_id,
        "gmail_status": get_gmail_connection_status(user),
    }


def _outreach_markdown(tool_payload: dict) -> str:
    agent = tool_payload.get("agent") or {}
    email = agent.get("email") or {}
    if agent.get("type") == "draft_email":
        return (
            "I used the email outreach specialist to prepare a Gmail-ready draft. "
            "Review and edit it in the outreach card below, then save it as a Gmail draft or send only after confirmation.\n\n"
            f"**Subject:** {email.get('subject', '')}"
        )
    return (
        f"I opened the email outreach workflow, but need more context: {agent.get('summary', '')} "
        "Use the outreach card below to add a recipient or search Gmail threads."
    )

def _serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }


def _serialize_conversation(conv: Conversation) -> dict:
    return {
        "id": conv.id,
        "title": conv.title,
        "updated_at": conv.updated_at.isoformat(),
    }


def _get_or_create_active(user):
    conv = Conversation.objects.filter(creator=user).order_by("-updated_at").first()
    if not conv:
        conv = Conversation.objects.create(creator=user)
    return conv


@login_required
@require_GET
def list_conversations(request):
    convs = Conversation.objects.filter(creator=request.user).order_by("-updated_at")
    return JsonResponse({"conversations": [_serialize_conversation(c) for c in convs]})


@login_required
@require_POST
def new_conversation(request):
    conv = Conversation.objects.create(creator=request.user)
    return JsonResponse(_serialize_conversation(conv), status=201)


@login_required
@require_POST
def delete_conversation(request, conversation_id):
    conv = get_object_or_404(Conversation, id=conversation_id, creator=request.user)
    conv.delete()
    return JsonResponse({"status": "ok"})


@login_required
@require_GET
def conversation_history(request):
    conversation_id = request.GET.get("conversation_id")
    if conversation_id:
        conv = get_object_or_404(Conversation, id=conversation_id, creator=request.user)
    else:
        conv = _get_or_create_active(request.user)
    return JsonResponse({
        "conversation_id": conv.id,
        "title": conv.title,
        "messages": [_serialize_message(m) for m in conv.messages.all()],
    })


@login_required
@require_POST
def chat(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    content = (payload.get("message") or "").strip()
    if not content:
        return JsonResponse({"error": "Message is required."}, status=400)

    conversation_id = payload.get("conversation_id")
    if conversation_id:
        conv = get_object_or_404(Conversation, id=conversation_id, creator=request.user)
    else:
        conv = _get_or_create_active(request.user)

    # Auto-title from first user message
    if conv.title == "New chat" and not conv.messages.exists():
        conv.title = content[:60]
        conv.save(update_fields=["title", "updated_at"])

    user_message = Message.objects.create(
        conversation=conv, role=Message.ROLE_USER, content=content
    )
    if _looks_like_outreach_request(content):
        tool_payload = _run_main_agent_outreach_tool(request.user, content)
        assistant_content = _outreach_markdown(tool_payload)
    else:
        assistant_content = generate_creator_agent_reply(request.user, conv, content)
    assistant_message = Message.objects.create(
        conversation=conv, role=Message.ROLE_ASSISTANT, content=assistant_content
    )
    return JsonResponse(
        {
            "conversation_id": conv.id,
            "title": conv.title,
            "messages": [_serialize_message(user_message), _serialize_message(assistant_message)],
        },
        status=201,
    )


@login_required
@require_POST
def chat_stream(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    content = (payload.get("message") or "").strip()
    if not content:
        return JsonResponse({"error": "Message is required."}, status=400)

    conversation_id = payload.get("conversation_id")
    if conversation_id:
        conv = get_object_or_404(Conversation, id=conversation_id, creator=request.user)
    else:
        conv = _get_or_create_active(request.user)

    if conv.title == "New chat" and not conv.messages.exists():
        conv.title = content[:60]
        conv.save(update_fields=["title", "updated_at"])

    Message.objects.create(conversation=conv, role=Message.ROLE_USER, content=content)

    user = request.user

    def _event_stream():
        yield f"data: {json.dumps({'type': 'status', 'text': 'Thinking…'})}\n\n"
        if _looks_like_outreach_request(content):
            yield f"data: {json.dumps({'type': 'status', 'text': 'Opening the email outreach specialist…'})}\n\n"
            tool_payload = _run_main_agent_outreach_tool(user, content)
            assistant_content = _outreach_markdown(tool_payload)
            msg = Message.objects.create(
                conversation=conv, role=Message.ROLE_ASSISTANT, content=assistant_content
            )
            yield f"data: {json.dumps({'type': 'outreach', 'tool': tool_payload})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv.id, 'title': conv.title, 'message': _serialize_message(msg), 'text': assistant_content})}\n\n"
            return

        chunks = []
        for chunk in stream_creator_agent_reply(user, conv, content):
            chunks.append(chunk)
            yield f"data: {json.dumps({'type': 'chunk', 'text': chunk})}\n\n"
        full_text = "".join(chunks) or "I couldn't generate a response. Please try again."
        msg = Message.objects.create(
            conversation=conv, role=Message.ROLE_ASSISTANT, content=full_text
        )
        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv.id, 'title': conv.title, 'message': _serialize_message(msg)})}\n\n"

    resp = StreamingHttpResponse(_event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp
