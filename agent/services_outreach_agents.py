"""OpenAI Agents SDK specialists for Badger creator outreach.

The agents in this module are intentionally side-effect free: they can write,
revise, summarize, and suggest text, but they are not given Gmail tools and they
cannot create drafts or send email. Django views/services perform Gmail writes
only after explicit creator UI actions.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

from django.conf import settings

try:  # Prefer Pydantic v2 when installed by the project environment.
    from pydantic import BaseModel, Field, ValidationError
except Exception:  # pragma: no cover - lightweight fallback for local checks without deps.
    class ValidationError(Exception):
        pass

    class BaseModel:
        def __init__(self, **kwargs):
            annotations = getattr(self, "__annotations__", {})
            for name in annotations:
                setattr(self, name, kwargs.get(name))

        def model_dump(self):
            def convert(value):
                if isinstance(value, BaseModel):
                    return value.model_dump()
                if isinstance(value, list):
                    return [convert(v) for v in value]
                return value
            return {name: convert(getattr(self, name)) for name in getattr(self, "__annotations__", {})}

        @classmethod
        def model_validate(cls, value):
            if isinstance(value, cls):
                return value
            if not isinstance(value, dict):
                raise ValidationError("Invalid value")
            return cls(**value)

    def Field(default=None, default_factory=None, **kwargs):
        if default_factory is not None:
            return default_factory()
        return default

try:
    from agents import Agent, Runner
except Exception:  # pragma: no cover - dependency may be unavailable in CI until installed.
    Agent = None
    Runner = None

logger = logging.getLogger(__name__)
DEFAULT_OUTREACH_AGENT_MODEL = "gpt-4.1-mini"
VALID_TONES = {"professional", "casual", "concise", "enthusiastic"}


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
    summary: str
    email: EmailPayload = Field(default_factory=EmailPayload)
    items: list[AgentItem] = Field(default_factory=list)
    requires_user_approval: bool
    followup: str | None = None


def _error_output(message: str, followup: str | None = None) -> OutreachAgentOutput:
    return OutreachAgentOutput(
        type="error",
        summary=message,
        email=EmailPayload(to="", subject="", body=""),
        items=[],
        requires_user_approval=False,
        followup=followup,
    )


def _model_name() -> str:
    return (
        os.environ.get("OUTREACH_AGENT_MODEL")
        or os.environ.get("OPENAI_CREATOR_AGENT_MODEL")
        or getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_OUTREACH_AGENT_MODEL)
        or DEFAULT_OUTREACH_AGENT_MODEL
    ).strip()


EMAIL_WRITER_INSTRUCTIONS = """
You are Badger's Creator Outreach Email Writer. Your only job is to write and revise creator-to-business partnership outreach emails.
Use only the creator, business, and email context provided by the application. Do not invent recipient emails, metrics, offers,
affiliate terms, contact details, or prior relationships. Produce concise, natural outreach that a creator can safely edit and send.
You never send email, never create Gmail drafts, and never claim an email was sent. All send actions require explicit creator approval in the UI.
Return only the typed structured output requested by the application. For draft_email and reply_suggestion outputs,
requires_user_approval must be true and email.to must match the validated recipient provided by the application.
Never include raw HTML.
""".strip()

THREAD_SUMMARY_INSTRUCTIONS = """
You are Badger's Gmail Thread Summary Agent. Summarize creator-business email threads, identify business intent,
unanswered questions, risks, and practical next actions. You never send email and never create drafts. Return structured output only.
For summaries and next_actions, leave email fields empty unless explicitly asked to suggest a reply.
""".strip()

REPLY_INSTRUCTIONS = """
You are Badger's Creator Reply Suggestion Agent. Suggest concise, realistic replies to business responses using only the supplied
creator, business, and thread context. Do not invent metrics, terms, or commitments. You never send email and never create drafts.
Return type reply_suggestion, requires_user_approval true, and use the validated recipient supplied by the application.
""".strip()


def _build_agent(name: str, instructions: str):
    if Agent is None:
        raise RuntimeError("OpenAI Agents SDK is not installed. Add the openai-agents package to the environment.")
    return Agent(name=name, instructions=instructions, model=_model_name(), output_type=OutreachAgentOutput)


def _coerce_output(result: Any, expected_recipient: str | None = None) -> OutreachAgentOutput:
    raw = getattr(result, "final_output", result)
    try:
        if isinstance(raw, OutreachAgentOutput):
            output = raw
        elif isinstance(raw, str):
            output = OutreachAgentOutput.model_validate(json.loads(raw))
        elif isinstance(raw, dict):
            output = OutreachAgentOutput.model_validate(raw)
        else:
            dump = raw.model_dump() if hasattr(raw, "model_dump") else raw.dict()
            output = OutreachAgentOutput.model_validate(dump)
    except Exception as exc:
        logger.warning("Outreach agent returned invalid structured output: %s", exc)
        return _error_output("The outreach agent returned an invalid response. Please try again.")

    if output.type in {"draft_email", "reply_suggestion"}:
        output.requires_user_approval = True
        if expected_recipient is not None:
            output.email.to = expected_recipient
    if output.type in {"email_summary", "next_actions"} and not output.email:
        output.email = EmailPayload(to="", subject="", body="")
    return output


def run_email_writer(context: dict[str, Any], expected_recipient: str) -> OutreachAgentOutput:
    if not expected_recipient:
        return _error_output("A validated recipient email is required before writing outreach.")
    if Runner is None:
        return _error_output("The OpenAI Agents SDK is not installed on the server.", "Install openai-agents and try again.")
    prompt = (
        "Write or revise a creator-to-business partnership email from this JSON context. "
        "Return a structured draft_email or error output.\n"
        f"{json.dumps(context, default=str, ensure_ascii=False)}"
    )
    try:
        result = Runner.run_sync(_build_agent("EmailWritingAgent", EMAIL_WRITER_INSTRUCTIONS), prompt)
    except Exception as exc:
        logger.exception("EmailWritingAgent failed without exposing email body. user_context_keys=%s", list(context.keys()))
        return _error_output("The outreach agent could not generate an email right now. Please try again.", str(exc)[:180])
    return _coerce_output(result, expected_recipient=expected_recipient)


def run_thread_summary(thread_context: dict[str, Any], next_actions_only: bool = False) -> OutreachAgentOutput:
    if Runner is None:
        return _error_output("The OpenAI Agents SDK is not installed on the server.", "Install openai-agents and try again.")
    target = "next_actions" if next_actions_only else "email_summary"
    prompt = f"Summarize this Gmail thread and return a structured {target} output.\n{json.dumps(thread_context, default=str, ensure_ascii=False)}"
    try:
        result = Runner.run_sync(_build_agent("ThreadSummaryAgent", THREAD_SUMMARY_INSTRUCTIONS), prompt)
    except Exception as exc:
        logger.exception("ThreadSummaryAgent failed. thread_id=%s", thread_context.get("thread_id"))
        return _error_output("The outreach agent could not summarize this thread right now.", str(exc)[:180])
    return _coerce_output(result)


def run_reply_suggestion(context: dict[str, Any], expected_recipient: str) -> OutreachAgentOutput:
    if not expected_recipient:
        return _error_output("A validated recipient email is required before suggesting a reply.")
    if Runner is None:
        return _error_output("The OpenAI Agents SDK is not installed on the server.", "Install openai-agents and try again.")
    prompt = f"Suggest a creator reply and return a structured reply_suggestion output.\n{json.dumps(context, default=str, ensure_ascii=False)}"
    try:
        result = Runner.run_sync(_build_agent("ReplySuggestionAgent", REPLY_INSTRUCTIONS), prompt)
    except Exception as exc:
        logger.exception("ReplySuggestionAgent failed. thread_id=%s", context.get("thread", {}).get("id"))
        return _error_output("The outreach agent could not suggest a reply right now.", str(exc)[:180])
    return _coerce_output(result, expected_recipient=expected_recipient)
