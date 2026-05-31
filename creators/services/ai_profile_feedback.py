from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import requests
import os

from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def _inputs_fingerprint(inputs: dict[str, Any]) -> str:
    """Stable SHA-256 of the AI input dict. Same inputs → same hash."""
    canonical = json.dumps(inputs, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _error_feedback(payload: dict[str, Any], message: str, *, error_code: str = "ai_request_failed") -> dict[str, Any]:
    return {
        "overall_score": None,
        "verdict": "Analysis Failed",
        "summary": message,
        "dimension_scores": {},
        "top_priority_actions": [],
        "benchmark_comparison": {},
        "error": {"code": error_code, "message": message},
        "inputs": payload["inputs"],
    }


def _pending_feedback(payload: dict[str, Any], input_hash: str) -> dict[str, Any]:
    return {
        "overall_score": None,
        "verdict": "Analysis Pending",
        "summary": "AI profile feedback is being refreshed in the background.",
        "dimension_scores": {},
        "top_priority_actions": [],
        "benchmark_comparison": {},
        "inputs": payload["inputs"],
        "_input_hash": input_hash,
        "pending": True,
    }


def build_ai_profile_feedback(
    *,
    user,
    platform: str,
    account: dict[str, Any],
    summary_metrics: dict[str, Any],
    audience: dict[str, Any],
    performance: dict[str, Any],
    cached_hash: str | None = None,
    cached_feedback: dict[str, Any] | None = None,
    allow_api_request: bool = True,
) -> dict[str, Any]:
    creator_meta = getattr(user, "creatormeta", None)
    paid_deals = _safe_int(getattr(creator_meta, "paid_brand_deals_count", 0))
    gifted_deals = _safe_int(getattr(creator_meta, "gifted_brand_deals_count", 0))
    affiliate_deals = _safe_int(getattr(creator_meta, "affiliate_brand_deals_count", 0))
    total_brand_deals = paid_deals + gifted_deals + affiliate_deals
    profile_visit_conversion = _safe_float(summary_metrics.get("average_profile_visit_rate"))
    sponsored_conversion = _safe_float(getattr(creator_meta, "avg_sponsored_conversion_rate_pct", 0.0))
    profile_payload = {
        "inputs": {
            "platform": platform,
            "niche": ", ".join(getattr(creator_meta, "niches", None) or []) or "general",
            "follower_count": _safe_int(account.get("followers_count")),
            "engagement_rate_pct": _safe_float(summary_metrics.get("average_engagement_rate")),
            "posting_frequency": _safe_float(summary_metrics.get("post_frequency_weekly")),
            "audience_age_range": [row.get("label") for row in (audience.get("top_age_groups") or [])[:3]],
            "audience_gender_split": audience.get("gender_split") or {},
            "audience_concentration_pct": _safe_float(audience.get("percent_top_city_followers")),
            "conversion_rate_pct": sponsored_conversion or profile_visit_conversion,
            "save_rate_pct": _safe_float(summary_metrics.get("average_save_rate")),
            "past_brand_deals_count": total_brand_deals,
            "paid_brand_deals_count": paid_deals,
            "gifted_brand_deals_count": gifted_deals,
            "affiliate_brand_deals_count": affiliate_deals,
            "content_style_description": (creator_meta.bio if creator_meta else "") or account.get("biography") or "",
            "partnership_history": (getattr(creator_meta, "partnership_history_notes", "") or "").strip(),
            "reach": _safe_int(performance.get("reach")),
        }
    }

    input_hash = _inputs_fingerprint(profile_payload["inputs"])

    # Return the cached score when nothing in the inputs has changed.
    if cached_hash == input_hash and cached_feedback and not cached_feedback.get("error"):
        logger.debug(
            "AI profile feedback cache hit for user_id=%s platform=%s hash=%s",
            user.id, platform, input_hash[:12],
        )
        result = dict(cached_feedback)
        result["_input_hash"] = input_hash
        return result

    if not allow_api_request:
        if cached_feedback and not cached_feedback.get("error"):
            result = dict(cached_feedback)
            result["_input_hash"] = cached_hash or input_hash
            result["stale"] = True
            return result
        return _pending_feedback(profile_payload, input_hash)

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        msg = "OpenAI API key missing: set OPENAI_API_KEY in server environment."
        logger.error("AI profile feedback disabled for user_id=%s platform=%s: %s", user.id, platform, msg)
        return _error_feedback(profile_payload, msg, error_code="missing_api_key")
    logger.info(
        "AI profile feedback recomputing for user_id=%s platform=%s (inputs changed)",
        user.id, platform,
    )

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": os.environ.get("OPENAI_SOCIAL_ANALYZER_MODEL", "gpt-4.1-mini").strip(),
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": "Return JSON only for creator profile evaluation."},
                    {"role": "user", "content": json.dumps({
                        "task": "Score creator profile health across 4 dimensions using holistic reasoning: Engagement (0-20), Audience Quality (0-25), Growth Activity (0-25), Monetization (0-30). Use engagement rate as primary engagement signal (<2% warning, >4% strong, >8% exceptional). Treat missing audience fields as negative trust gaps. For monetization, distinguish untested potential (strong profile but no deals) from weak performance (low engagement + no deals + low activity). Gifted deals count less than paid deals. Always include concrete number-referenced rationale and specific next steps.",
                        "inputs": profile_payload["inputs"],
                        "output_schema": {
                            "overall_score": "number 0-100",
                            "verdict": "string",
                            "summary": "string",
                            "dimension_scores": {
                                "engagement": {"score": "0-20", "rationale": "string", "actions": ["string"]},
                                "audience_quality": {"score": "0-25", "rationale": "string", "actions": ["string"]},
                                "growth_activity": {"score": "0-25", "rationale": "string", "actions": ["string"]},
                                "monetization": {"score": "0-30", "rationale": "string", "actions": ["string"]}
                            },
                            "top_priority_actions": ["string"],
                            "benchmark_comparison": "object"
                        }
                    })},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        if response.status_code >= 400:
            logger.error(
                "OpenAI API request failed for user_id=%s platform=%s status=%s body=%s",
                user.id,
                platform,
                response.status_code,
                (response.text or "")[:1000],
            )
            return _error_feedback(
                profile_payload,
                f"OpenAI API error ({response.status_code}). Check API key, model, and account permissions.",
                error_code="openai_http_error",
            )
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        parsed["inputs"] = profile_payload["inputs"]
        parsed["_input_hash"] = input_hash
        return parsed
    except requests.Timeout as exc:
        logger.exception("OpenAI API timeout for user_id=%s platform=%s", user.id, platform)
        return _error_feedback(profile_payload, f"OpenAI API timeout: {exc}", error_code="openai_timeout")
    except requests.RequestException as exc:
        logger.exception("OpenAI API transport error for user_id=%s platform=%s", user.id, platform)
        return _error_feedback(profile_payload, f"OpenAI API request error: {exc}", error_code="openai_request_error")
    except json.JSONDecodeError as exc:
        logger.exception("OpenAI response JSON parse error for user_id=%s platform=%s", user.id, platform)
        return _error_feedback(profile_payload, f"OpenAI response parsing error: {exc}", error_code="openai_parse_error")
    except Exception as exc:
        logger.exception("Unexpected AI feedback error for user_id=%s platform=%s", user.id, platform)
        return _error_feedback(profile_payload, f"Unexpected AI evaluation error: {exc}", error_code="unexpected_error")


def refresh_ai_score_if_stale(user_id: int) -> bool:
    """
    Re-run the AI analyzer using the stored Instagram snapshot data plus
    fresh creator_meta values. Called in a background thread after profile
    saves so the score stays in sync without blocking the response.

    Returns True if the snapshot was updated with a new score.
    """
    from creators.models import SocialAnalyticsSnapshot

    User = get_user_model()
    try:
        user = User.objects.select_related("creatormeta").get(pk=user_id)
    except User.DoesNotExist:
        return False

    snapshot = SocialAnalyticsSnapshot.objects.filter(
        user=user,
        platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
    ).first()
    if snapshot is None or not snapshot.payload:
        return False

    payload = snapshot.payload
    ai_cache = payload.get("_ai_cache") or {}
    account = payload.get("profile") or {}
    summary_metrics = payload.get("summary_metrics") or {}
    audience = payload.get("audience") or {}
    account_metrics = payload.get("account_metrics") or {}

    feedback = build_ai_profile_feedback(
        user=user,
        platform=SocialAnalyticsSnapshot.PLATFORM_INSTAGRAM,
        account=account,
        summary_metrics=summary_metrics,
        audience=audience,
        performance={
            "reach": int(account_metrics.get("reach_1d") or 0),
            "profile_visits": int(account_metrics.get("profile_views") or 0),
            "website_clicks": int(account_metrics.get("website_clicks") or 0),
        },
        cached_hash=ai_cache.get("hash"),
        cached_feedback=ai_cache.get("feedback"),
    )

    new_hash = feedback.get("_input_hash")
    if new_hash and new_hash != ai_cache.get("hash") and not feedback.get("error"):
        payload["_ai_cache"] = {"hash": new_hash, "feedback": feedback}
        snapshot.payload = payload
        snapshot.save(update_fields=["payload"])
        logger.info("AI score refreshed for user_id=%s after profile change", user_id)
        return True

    return False
