from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from creatorMatch.services.matching.types import MatchConfig

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
logger = logging.getLogger(__name__)


def _extract_output_text(payload: dict[str, Any]) -> str:
    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = payload.get("output") or []
    for item in output:
        for content in item.get("content") or []:
            if content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text
    return ""


def _build_bulk_prompt(candidates: list[dict[str, Any]], business_context: dict[str, Any]) -> str:
    payload = {"candidates": candidates, "business": business_context}
    return (
        "You are a creator-brand partnership analyst. Score each creator candidate. "
        "Return strict JSON: {\"results\": [{\"creator_id\": int, \"score\": int 0-100, \"reasoning\": string, \"highlights\": [string up to 3]}]}. "
        "No markdown. Keep reasoning concise.\n\n"
        f"Data:\n{json.dumps(payload, ensure_ascii=False)}"
    )


def score_candidates_with_ai(
    candidates: list[dict[str, Any]],
    business_context: dict[str, Any],
    config: MatchConfig,
    timeout_seconds: int = 18,
) -> dict[int, dict[str, Any]]:
    results, _ = score_candidates_with_ai_diagnostics(
        candidates=candidates,
        business_context=business_context,
        config=config,
        timeout_seconds=timeout_seconds,
    )
    return results


def score_candidates_with_ai_diagnostics(
    candidates: list[dict[str, Any]],
    business_context: dict[str, Any],
    config: MatchConfig,
    timeout_seconds: int = 18,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key or not candidates:
        if not api_key:
            logger.warning("AI scoring skipped: OPENAI_API_KEY is missing or empty.")
        return {}, {
            "ai_attempted": False,
            "error_code": "missing_api_key" if not api_key else "no_candidates",
            "error_message": "OPENAI_API_KEY missing." if not api_key else "No candidates sent to AI.",
        }

    prompt = _build_bulk_prompt(candidates, business_context)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {
        "model": os.environ.get("OPENAI_MATCH_MODEL", config.model),
        "input": prompt,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "bulk_creator_match_scores",
                "schema": {
                    "type": "object",
                    "properties": {
                        "results": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "creator_id": {"type": "integer"},
                                    "score": {"type": "integer", "minimum": 0, "maximum": 100},
                                    "reasoning": {"type": "string"},
                                    "highlights": {"type": "array", "items": {"type": "string"}, "maxItems": 3},
                                },
                                "required": ["creator_id", "score", "reasoning", "highlights"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["results"],
                    "additionalProperties": False,
                },
                "strict": True,
            }
        },
    }
    try:
        response = requests.post(OPENAI_RESPONSES_URL, headers=headers, json=body, timeout=timeout_seconds)
        response.raise_for_status()
        response_payload = response.json()
        output_text = _extract_output_text(response_payload)
        if not output_text:
            logger.warning("AI scoring returned no output text. response_id=%s", response_payload.get("id"))
            return {}, {
                "ai_attempted": True,
                "error_code": "empty_output",
                "error_message": "OpenAI returned no output text.",
                "response_id": response_payload.get("id"),
            }
        parsed = json.loads(output_text)
        results: dict[int, dict[str, Any]] = {}
        for row in parsed.get("results") or []:
            creator_id = int(row.get("creator_id"))
            reasoning = str(row.get("reasoning") or "").strip()
            if not reasoning:
                continue
            highlights = [str(item).strip() for item in (row.get("highlights") or []) if str(item).strip()]
            results[creator_id] = {
                "score": max(0, min(100, int(row.get("score")))),
                "reasoning": reasoning,
                "highlights": highlights[:3],
            }
        return results, {
            "ai_attempted": True,
            "error_code": None,
            "error_message": None,
            "response_id": response_payload.get("id"),
        }
    except (requests.RequestException, ValueError, json.JSONDecodeError, TypeError) as exc:
        logger.exception("AI scoring request failed: %s", exc)
        return {}, {
            "ai_attempted": True,
            "error_code": "request_or_parse_failure",
            "error_message": str(exc),
        }
