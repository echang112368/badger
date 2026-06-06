"""OpenAI Agents SDK specialists for Badger creator outreach.

The agents in this module are intentionally scoped to preparation work only.
They do not receive Gmail send tools and cannot send email; Django views call
Gmail draft/send service methods only after explicit creator UI actions.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from asgiref.sync import async_to_sync
from django.conf import settings
from pydantic import BaseModel, Field, ValidationError, field_validator

DEFAULT_OUTREACH_AGENT_MODEL = "gpt-4.1-mini"
VALID_OUTPUT_TYPES = {"draft_email", "email_summary", "reply_suggestion", "next_actions", "error"}
APPROVAL_TYPES = {"draft_email", "reply_suggestion"}


class EmailPayload(BaseModel):
    to: str = ""
    subject: str = ""
    body: str = ""


class AgentItem(BaseModel):
    label: str
    detail: str
    cta: str | None = None


class OutreachAgentOutput(BaseModel):
    type: Literal["draft_email", "email_summary", "reply_suggestion", "next_actions", "error"]
    summary: str = ""
    email: EmailPayload = Field(default_factory=EmailPayload)
    items: list[AgentItem] = Field(default_factory=list)
    requires_user_approval: bool = False
    followup: str | None = None

    @field_validator("requires_user_approval")
    @classmethod
    def approval_required_for_email_outputs(cls, value: bool, info):
        output_type = (info.data or {}).get("type")
        if output_type in APPROVAL_TYPES:
            return True
        return value

    def normalized(self) -> "OutreachAgentOutput":
        if self.type in APPROVAL_TYPES and not self.requires_user_approval:
            self.requires_user_approval = True
        if self.type in {"email_summary", "next_actions", "error"} and self.email is None:
            self.email = EmailPayload()
        return self


def outreach_model_name() -> str:
    return (
        os.environ.get("OUTREACH_AGENT_MODEL")
        or os.environ.get("OPENAI_CREATOR_AGENT_MODEL")
        or getattr(settings, "OUTREACH_AGENT_MODEL", "")
        or getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", "")
        or DEFAULT_OUTREACH_AGENT_MODEL
    ).strip()


EMAIL_WRITER_INSTRUCTIONS = """
You are Badger's Creator Outreach Email Writer. Your only job is to write and revise creator-to-business partnership outreach emails.
Use only the creator, business, thread, and email context provided by the application.
Do not invent recipient emails, metrics, offers, contact details, or prior relationships.
Personalize with creator profile data such as username, first name, bio, short_pitch, social platforms, follower_range, social profiles, content skills, niches, country, languages, deal counts, conversion rate, and partnership history notes when provided.
Personalize with business data such as company name, marketplace status, store/profile details, affiliate relationship data, and known terms when provided.
Keep emails concise, specific, realistic, plain text, and easy for a creator to edit.
Supported tones are professional, casual, concise, and enthusiastic.
Never include raw HTML. Never state an email has been sent. Never instruct Gmail to send. Never ask for Gmail credentials.
All send actions require explicit creator approval in the UI. Return only the typed structured output requested by the application.
For draft_email and reply_suggestion outputs, requires_user_approval must be true.
If the recipient email is missing or invalid in the application context, return type="error" with a followup instead of inventing one.
""".strip()

THREAD_SUMMARY_INSTRUCTIONS = """
You are Badger's Gmail Thread Summary Agent. Summarize normalized Gmail threads for creator-business partnership outreach.
Extract business intent, unanswered questions, risks, timeline signals, and next actions.
Do not write raw HTML, do not send email, and do not claim any action was taken.
Return only the typed structured output requested by the application.
""".strip()

REPLY_SUGGESTION_INSTRUCTIONS = """
You are Badger's Creator Reply Suggestion Agent. Suggest creator replies to business responses using only the creator, business, and Gmail thread context provided.
Keep replies concise, specific, plain text, and safe for manual review.
Do not invent metrics, terms, offers, or prior relationships. Do not send email or state email was sent.
For reply_suggestion outputs, requires_user_approval must be true. Return only the typed structured output requested by the application.
""".strip()


def _build_agent(instructions: str):
    from agents import Agent

    return Agent(
        name="Badger Creator Outreach Specialist",
        model=outreach_model_name(),
        instructions=instructions,
        output_type=OutreachAgentOutput,
    )


def _run_agent(instructions: str, payload: dict[str, Any]) -> OutreachAgentOutput:
    from agents import Runner

    agent = _build_agent(instructions)
    prompt = json.dumps(payload, ensure_ascii=False, default=str)
    result = async_to_sync(Runner.run)(agent, prompt)
    final_output = getattr(result, "final_output", result)
    if isinstance(final_output, OutreachAgentOutput):
        return final_output.normalized()
    if isinstance(final_output, dict):
        return OutreachAgentOutput.model_validate(final_output).normalized()
    if isinstance(final_output, str):
        return OutreachAgentOutput.model_validate_json(final_output).normalized()
    return OutreachAgentOutput.model_validate(final_output).normalized()


def _error_output(message: str, followup: str | None = None) -> OutreachAgentOutput:
    return OutreachAgentOutput(
        type="error",
        summary=message,
        email=EmailPayload(),
        items=[],
        requires_user_approval=False,
        followup=followup,
    )


def _safe_run(instructions: str, payload: dict[str, Any]) -> OutreachAgentOutput:
    try:
        return _run_agent(instructions, payload)
    except (ImportError, ValidationError, ValueError, TypeError) as exc:
        return _error_output(
            "The outreach agent could not return a valid structured response.",
            str(exc)[:240],
        )
    except Exception as exc:
        return _error_output("The outreach agent is unavailable right now.", str(exc)[:240])


def generate_outreach_email(payload: dict[str, Any]) -> OutreachAgentOutput:
    return _safe_run(EMAIL_WRITER_INSTRUCTIONS, {"task": "generate_outreach_email", **payload})


def revise_outreach_email(payload: dict[str, Any]) -> OutreachAgentOutput:
    return _safe_run(EMAIL_WRITER_INSTRUCTIONS, {"task": "revise_outreach_email", **payload})


def summarize_thread(payload: dict[str, Any]) -> OutreachAgentOutput:
    return _safe_run(THREAD_SUMMARY_INSTRUCTIONS, {"task": "summarize_thread", **payload})


def suggest_reply(payload: dict[str, Any]) -> OutreachAgentOutput:
    return _safe_run(REPLY_SUGGESTION_INSTRUCTIONS, {"task": "suggest_reply", **payload})


def extract_next_actions(payload: dict[str, Any]) -> OutreachAgentOutput:
    return _safe_run(THREAD_SUMMARY_INSTRUCTIONS, {"task": "extract_next_actions", **payload})
