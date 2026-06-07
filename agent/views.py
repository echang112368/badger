import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .openai_client import generate_creator_agent_reply, stream_creator_agent_reply
from .services.rate_calculator import calculate_creator_rate


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
    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        attachments = []

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
    assistant_content = generate_creator_agent_reply(request.user, conv, content, attachments=attachments)
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
    attachments = payload.get("attachments")
    if not isinstance(attachments, list):
        attachments = []

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
        chunks = []
        for chunk in stream_creator_agent_reply(user, conv, content, attachments=attachments):
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


@login_required
@require_POST
def rate_calculator_calculate(request):
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON payload."}, status=400)

    result = calculate_creator_rate(payload)
    status = 400 if result.get("invalid_inputs") else 200
    return JsonResponse(result, status=status)
