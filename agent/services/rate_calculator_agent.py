"""OpenAI Agents SDK wrapper for the structured creator rate calculator."""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
from typing import Any

from django.conf import settings

from agent.services.rate_calculator import calculate_creator_rate

DEFAULT_RATE_CALCULATOR_AGENT_MODEL = "gpt-4.1-mini"

RATE_CALCULATOR_INSTRUCTIONS = """
You are Badger's RateCalculatorAgent for creator campaign pricing.
You are not a vague chatbot. Your job is to collect missing pricing inputs,
call the calculate_creator_rate tool, and return the tool's structured JSON.

Rules:
- Base pricing is driven primarily by average views, not follower count.
- Never invent important pricing inputs. If the tool returns missing_inputs, ask
  concise follow-up questions for those fields.
- If only minor metrics are missing, use the conservative assumptions returned
  by the tool and label them clearly.
- Always explain the math in plain English and keep the line-item breakdown.
- Always say creator rates are not standardized and the output is a data-backed
  estimate, not a guaranteed market price.
- Do not give legal advice. Recommend consulting a lawyer or manager for large
  contracts, exclusivity, perpetual usage, full buyouts, or long-term ambassador deals.
- Return structured JSON matching the tool output whenever a calculation is possible.
""".strip()


def _model_name() -> str:
    return (
        os.environ.get("RATE_CALCULATOR_AGENT_MODEL")
        or os.environ.get("OPENAI_CREATOR_AGENT_MODEL")
        or getattr(settings, "OPENAI_CREATOR_AGENT_MODEL", DEFAULT_RATE_CALCULATOR_AGENT_MODEL)
        or DEFAULT_RATE_CALCULATOR_AGENT_MODEL
    ).strip()


def _load_agents_sdk():
    if importlib.util.find_spec("agents") is None:
        raise RuntimeError("OpenAI Agents SDK is not installed. Add the openai-agents package to the environment.")
    return importlib.import_module("agents")


def build_rate_calculator_agent():
    """Build the dedicated Agents SDK agent with calculate_creator_rate as a tool."""
    agents = _load_agents_sdk()
    tool = agents.function_tool(calculate_creator_rate)
    return agents.Agent(
        name="RateCalculatorAgent",
        instructions=RATE_CALCULATOR_INSTRUCTIONS,
        model=_model_name(),
        tools=[tool],
    )


def run_rate_calculator_agent(context: dict[str, Any]) -> dict[str, Any]:
    """Run the dedicated agent and coerce JSON output for callers that need LLM follow-up behavior."""
    agents = _load_agents_sdk()
    prompt = (
        "Review this creator campaign pricing context. If important information is missing, "
        "ask for it. If enough information is present, call calculate_creator_rate and return JSON.\n"
        f"{json.dumps(context, default=str, ensure_ascii=False)}"
    )
    result = agents.Runner.run_sync(build_rate_calculator_agent(), prompt)
    raw = getattr(result, "final_output", result)
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if isinstance(raw, str):
        return json.loads(raw)
    return {"creator_explanation": str(raw)}
