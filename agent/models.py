from django.conf import settings
from django.db import models


class Conversation(models.Model):
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="agent_conversations",
    )
    title = models.CharField(max_length=120, default="New chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"{self.title} for {self.creator}"


class Message(models.Model):
    ROLE_USER = "user"
    ROLE_ASSISTANT = "assistant"
    ROLE_CHOICES = [
        (ROLE_USER, "User"),
        (ROLE_ASSISTANT, "Assistant"),
    ]

    conversation = models.ForeignKey(
        Conversation,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.role} message for conversation {self.conversation_id}"


class OutreachDraft(models.Model):
    STATUS_GENERATED = "generated"
    STATUS_EDITED = "edited"
    STATUS_GMAIL_DRAFTED = "gmail_drafted"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_GENERATED, "Generated"),
        (STATUS_EDITED, "Edited"),
        (STATUS_GMAIL_DRAFTED, "Gmail drafted"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outreach_drafts",
    )
    business = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_drafts",
    )
    recipient_email = models.EmailField()
    subject = models.CharField(max_length=255, blank=True)
    body = models.TextField(blank=True)
    tone = models.CharField(max_length=32, blank=True)
    gmail_draft_id = models.CharField(max_length=255, blank=True)
    gmail_thread_id = models.CharField(max_length=255, blank=True)
    gmail_message_id = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_GENERATED)
    last_agent_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        return f"Outreach draft {self.id} to {self.recipient_email}"


class RateReport(models.Model):
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rate_reports",
    )
    report_data = models.JSONField(default=dict)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"RateReport {self.id} for {self.creator_id}"


class OutreachThreadSummary(models.Model):
    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outreach_thread_summaries",
    )
    business = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_thread_summaries",
    )
    gmail_thread_id = models.CharField(max_length=255)
    summary = models.TextField(blank=True)
    next_actions = models.JSONField(default=list, blank=True)
    last_message_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        unique_together = ("creator", "gmail_thread_id")


class OutreachAgentInteraction(models.Model):
    ACTION_GENERATE = "generate"
    ACTION_REVISE = "revise"
    ACTION_SUMMARIZE_THREAD = "summarize_thread"
    ACTION_SUGGEST_REPLY = "suggest_reply"
    ACTION_NEXT_ACTIONS = "next_actions"
    ACTION_CHOICES = [
        (ACTION_GENERATE, "Generate"),
        (ACTION_REVISE, "Revise"),
        (ACTION_SUMMARIZE_THREAD, "Summarize thread"),
        (ACTION_SUGGEST_REPLY, "Suggest reply"),
        (ACTION_NEXT_ACTIONS, "Next actions"),
    ]

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="outreach_agent_interactions",
    )
    business = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="creator_outreach_agent_interactions",
    )
    action_type = models.CharField(max_length=32, choices=ACTION_CHOICES)
    safe_input = models.JSONField(default=dict, blank=True)
    structured_output = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
