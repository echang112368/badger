import json
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from instagram_connect.models import InstagramConnection

from .models import Conversation, Message
from .openai_client import OPENAI_RESPONSES_URL


class AgentAPITests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="agent_creator",
            password="pass123",
            email="agent_creator@example.com",
            is_creator=True,
        )
        self.client.force_login(self.creator)

    def test_creator_dashboard_renders_agent_tab_context(self):
        response = self.client.get(reverse("creator_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-dashboard-tab="agent"')
        self.assertContains(response, reverse("creator_dashboard") + "?tab=agent")
        self.assertContains(response, reverse("agent:history"))
        self.assertTrue(Conversation.objects.filter(creator=self.creator).exists())

    def test_creator_dashboard_agent_query_opens_agent_panel(self):
        response = self.client.get(reverse("creator_dashboard") + "?tab=agent")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'bi bi-chat-dots')
        self.assertContains(response, 'Chat with your creator agent')
        self.assertContains(response, 'id="dashboard-tab-agent" class="dashboard-tab-panel"')
        self.assertContains(response, 'id="dashboard-tab-dashboard" class="dashboard-tab-panel d-none"')

    def test_history_gets_creator_conversation(self):
        conversation = Conversation.objects.create(creator=self.creator)
        Message.objects.create(
            conversation=conversation,
            role=Message.ROLE_ASSISTANT,
            content="Welcome back.",
        )

        response = self.client.get(reverse("agent:history"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["conversation_id"], conversation.id)
        self.assertEqual(payload["messages"][0]["content"], "Welcome back.")

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key", "OPENAI_CREATOR_AGENT_MODEL": "gpt-4.1-mini"})
    @patch("agent.openai_client.requests.post")
    def test_chat_uses_openai_and_persists_messages(self, mock_post):
        InstagramConnection.objects.create(
            user=self.creator,
            instagram_user_id="ig-1",
            instagram_username="agentcreator",
            followers_count=1234,
            media_count=25,
        )
        mock_response = Mock()
        mock_response.json.return_value = {"output_text": "OpenAI-generated plan for @agentcreator."}
        mock_response.raise_for_status.return_value = None
        mock_post.return_value = mock_response

        response = self.client.post(
            reverse("agent:chat"),
            data=json.dumps({"message": "What should I do next?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(len(payload["messages"]), 2)
        self.assertEqual(payload["messages"][0]["role"], Message.ROLE_USER)
        self.assertEqual(payload["messages"][1]["role"], Message.ROLE_ASSISTANT)
        self.assertEqual(payload["messages"][1]["content"], "OpenAI-generated plan for @agentcreator.")
        self.assertEqual(Message.objects.filter(conversation__creator=self.creator).count(), 2)
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(mock_post.call_args.args[0], OPENAI_RESPONSES_URL)
        self.assertEqual(call_kwargs["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(call_kwargs["json"]["model"], "gpt-4.1-mini")
        self.assertIn("agentcreator", call_kwargs["json"]["input"])

    @patch.dict("os.environ", {}, clear=True)
    def test_chat_without_openai_key_returns_configuration_message(self):
        response = self.client.post(
            reverse("agent:chat"),
            data=json.dumps({"message": "Can you help?"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("OPENAI_API_KEY", payload["messages"][1]["content"])
