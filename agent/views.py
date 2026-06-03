import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST

from .models import Conversation, Message
from .openai_client import generate_creator_agent_reply


def _serialize_message(message: Message) -> dict:
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at.isoformat(),
    }


@login_required
@require_GET
def conversation_history(request):
    conversation, _ = Conversation.objects.get_or_create(creator=request.user)
    messages = conversation.messages.all()
    return JsonResponse(
        {
            "conversation_id": conversation.id,
            "messages": [_serialize_message(message) for message in messages],
        }
    )

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

    conversation, _ = Conversation.objects.get_or_create(creator=request.user)
    user_message = Message.objects.create(
        conversation=conversation,
        role=Message.ROLE_USER,
        content=content,
    )
    assistant_message = Message.objects.create(
        conversation=conversation,
        role=Message.ROLE_ASSISTANT,
        content=generate_creator_agent_reply(request.user, conversation, content),
    )
    return JsonResponse(
        {
            "conversation_id": conversation.id,
            "messages": [
                _serialize_message(user_message),
                _serialize_message(assistant_message),
            ],
        },
        status=201,
    )
