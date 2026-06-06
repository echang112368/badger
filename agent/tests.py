import json
from unittest.mock import Mock, patch

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from instagram_connect.models import InstagramConnection

from merchants.models import MerchantMeta

from .models import Conversation, Message, OutreachDraft
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

    @patch("agent.openai_client.gmail_service.create_draft")
    @patch("agent.openai_client.run_email_writer")
    def test_gmail_slash_command_writes_gmail_draft(self, mock_writer, mock_create_draft):
        from agent.services.outreach_agents import EmailPayload, OutreachAgentOutput

        mock_writer.return_value = OutreachAgentOutput(
            type="draft_email",
            summary="Draft ready.",
            email=EmailPayload(to="brand@example.com", subject="Partnership idea", body="Hi Brand,"),
            items=[],
            requires_user_approval=True,
            followup=None,
        )
        mock_create_draft.return_value = {"draft_id": "gmail-draft-1", "message_id": "msg-1", "thread_id": "thread-1"}

        response = self.client.post(
            reverse("agent:chat"),
            data=json.dumps({"message": "/gmail write a professional partnership email to brand@example.com about outerwear"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        assistant_message = payload["messages"][1]["content"]
        self.assertIn("created a Gmail draft", assistant_message)
        self.assertIn("gmail-draft-1", assistant_message)
        mock_writer.assert_called_once()
        mock_create_draft.assert_called_once_with(self.creator, "brand@example.com", "Partnership idea", "Hi Brand,")
        draft = OutreachDraft.objects.get(creator=self.creator, recipient_email="brand@example.com")
        self.assertEqual(draft.status, OutreachDraft.STATUS_GMAIL_DRAFTED)
        self.assertEqual(draft.gmail_draft_id, "gmail-draft-1")

    @patch("agent.openai_client.gmail_service.create_draft")
    @patch("agent.openai_client.run_email_writer")
    def test_gmail_slash_command_requires_valid_recipient_before_agent_or_gmail(self, mock_writer, mock_create_draft):
        response = self.client.post(
            reverse("agent:chat"),
            data=json.dumps({"message": "/gmail write this partnership email"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertIn("valid recipient email", payload["messages"][1]["content"])
        mock_writer.assert_not_called()
        mock_create_draft.assert_not_called()


class OutreachAgentTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="outreach_creator",
            password="pass123",
            email="outreach_creator@example.com",
            is_creator=True,
        )
        self.merchant_user = CustomUser.objects.create_user(
            username="merchant_outreach",
            password="pass123",
            email="merchant@example.com",
            is_merchant=True,
        )
        self.merchant_meta, _ = MerchantMeta.objects.get_or_create(user=self.merchant_user)
        self.merchant_meta.company_name = "Merchant Co"
        self.merchant_meta.marketplace_enabled = True
        self.merchant_meta.save()

    def test_integrated_outreach_actions_require_login(self):
        response = self.client.post(reverse("creator_outreach_generate"), data="{}", content_type="application/json")
        self.assertEqual(response.status_code, 302)

    def test_main_agent_page_embeds_outreach_tool_and_uses_gmail_status_without_tokens(self):
        from creators.models import GmailOAuthCredential
        from creators.services.gmail_oauth import encode_token

        GmailOAuthCredential.objects.create(
            user=self.creator,
            gmail_email="creator@gmail.com",
            access_token=encode_token("secret-access-token"),
            refresh_token=encode_token("secret-refresh-token"),
            status=GmailOAuthCredential.STATUS_CONNECTED,
        )
        self.client.force_login(self.creator)
        response = self.client.get(reverse("creator_agent"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "creator@gmail.com")
        self.assertNotContains(response, "secret-access-token")
        self.assertNotContains(response, "secret-refresh-token")
        self.assertContains(response, "Outreach Email Tool")
        self.assertContains(response, reverse("creator_gmail_disconnect"))
        self.assertNotContains(response, "/creators/outreach-agent/")

    @patch("agent.views_outreach.run_email_writer")
    def test_generate_endpoint_returns_structured_json_and_local_draft(self, mock_writer):
        from agent.services.outreach_agents import EmailPayload, OutreachAgentOutput

        mock_writer.return_value = OutreachAgentOutput(
            type="draft_email",
            summary="Draft ready.",
            email=EmailPayload(to="merchant@example.com", subject="Partnership idea", body="Hi Merchant Co,"),
            items=[],
            requires_user_approval=True,
            followup=None,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("creator_outreach_generate"),
            data=json.dumps({"business_id": self.merchant_user.id, "recipient_email": "merchant@example.com", "tone": "professional"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["output"]["type"], "draft_email")
        self.assertTrue(payload["output"]["requires_user_approval"])
        self.assertTrue(OutreachDraft.objects.filter(creator=self.creator, recipient_email="merchant@example.com").exists())

    def test_generate_validates_recipient(self):
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("creator_outreach_generate"),
            data=json.dumps({"business_id": self.merchant_user.id, "recipient_email": "not-email", "tone": "professional"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_email")

    @patch("agent.views_outreach.gmail_service.create_draft")
    def test_save_draft_uses_gmail_service(self, mock_create_draft):
        mock_create_draft.return_value = {"draft_id": "draft-1", "message_id": "msg-1", "thread_id": "thread-1"}
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("creator_outreach_save_draft"),
            data=json.dumps({"recipient_email": "merchant@example.com", "subject": "Subject", "body": "Body"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["gmail"]["draft_id"], "draft-1")
        mock_create_draft.assert_called_once()
        self.assertEqual(mock_create_draft.call_args.args[0], self.creator)

    def test_send_requires_explicit_confirmation(self):
        draft = OutreachDraft.objects.create(
            creator=self.creator,
            recipient_email="merchant@example.com",
            subject="Subject",
            body="Body",
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("creator_outreach_send"),
            data=json.dumps({"draft_id": draft.id, "confirm": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "confirm_required")

    def test_send_refuses_another_users_draft(self):
        other = CustomUser.objects.create_user(username="other_creator", password="pass123", email="other@example.com", is_creator=True)
        draft = OutreachDraft.objects.create(creator=other, recipient_email="merchant@example.com", subject="Subject", body="Body")
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("creator_outreach_send"),
            data=json.dumps({"draft_id": draft.id, "confirm": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_reply_send_requires_confirm_true_to_send(self):
        self.client.force_login(self.creator)
        with patch("agent.views_outreach.gmail_service.reply_to_thread") as mock_reply, patch("agent.views_outreach.gmail_service.create_draft") as mock_draft:
            mock_draft.return_value = {"draft_id": "draft-2", "message_id": "msg-2", "thread_id": "thread-2"}
            response = self.client.post(
                reverse("creator_outreach_reply"),
                data=json.dumps({"thread_id": "thread-2", "recipient_email": "merchant@example.com", "subject": "Re: Subject", "body": "Reply"}),
                content_type="application/json",
            )
            self.assertEqual(response.status_code, 200)
            mock_reply.assert_not_called()
            mock_draft.assert_called_once()


    @patch("agent.openai_client.run_email_writer")
    def test_main_agent_routes_outreach_ask_to_specialist_without_gmail_side_effects(self, mock_writer):
        from agent.openai_client import generate_creator_agent_reply
        from agent.services.outreach_agents import EmailPayload, OutreachAgentOutput

        conversation = Conversation.objects.create(creator=self.creator)
        mock_writer.return_value = OutreachAgentOutput(
            type="draft_email",
            summary="Draft ready.",
            email=EmailPayload(to="merchant@example.com", subject="Partnership idea", body="Hi Merchant Co,"),
            items=[],
            requires_user_approval=True,
            followup=None,
        )

        reply = generate_creator_agent_reply(
            self.creator,
            conversation,
            "Draft a professional outreach email to Merchant Co at merchant@example.com",
        )

        self.assertIn("outreach email specialist", reply)
        self.assertIn("This has not been sent", reply)
        self.assertTrue(OutreachDraft.objects.filter(creator=self.creator, recipient_email="merchant@example.com").exists())
        mock_writer.assert_called_once()
