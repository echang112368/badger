from __future__ import annotations

import json
from typing import Any

import requests
from django.conf import settings


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


def _fallback_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    engagement_rate = _safe_float(payload["inputs"]["engagement_rate_pct"])
    base_score = min(100, max(0, round((engagement_rate * 7) + 30)))
    verdict = "Strong Profile" if base_score >= 75 else "Growing Profile" if base_score >= 50 else "Needs Work"
    return {
        "overall_score": base_score,
        "verdict": verdict,
        "summary": "AI feedback is currently using fallback logic. Connect OpenAI credentials for richer analysis.",
        "dimension_scores": {
            "engagement_health": {"score": min(25, round(engagement_rate * 4)), "diagnosis": "Engagement signal estimated from recent metrics.", "actions": ["Post consistently at peak audience hours", "Increase saves with evergreen content"]},
            "audience_quality": {"score": 15, "diagnosis": "Audience profile details are partially available.", "actions": ["Add country and city concentration details", "Document audience age + gender split"]},
            "content_consistency": {"score": 15, "diagnosis": "Posting consistency inferred from account-level metrics.", "actions": ["Publish on a fixed weekly cadence", "Repeat high-performing content formats"]},
            "brand_readiness": {"score": 15, "diagnosis": "Partnership proof points are limited in profile metadata.", "actions": ["Add past brand deals and outcomes", "Expand content style summary for brand fit"]},
        },
        "top_priority_actions": [
            "Improve engagement consistency with a repeatable posting schedule.",
            "Complete audience demographic and concentration data.",
            "Add brand partnership history with measurable results.",
        ],
        "benchmark_comparison": {
            "platform": payload["inputs"].get("platform") or "instagram",
            "niche": payload["inputs"].get("niche") or "general",
            "engagement_rate": payload["inputs"].get("engagement_rate_pct"),
            "conversion_rate": payload["inputs"].get("conversion_rate_pct"),
            "audience_concentration": payload["inputs"].get("audience_concentration_pct"),
            "save_activity": payload["inputs"].get("save_rate_pct"),
        },
    }


def build_ai_profile_feedback(*, user, platform: str, account: dict[str, Any], summary_metrics: dict[str, Any], audience: dict[str, Any], performance: dict[str, Any]) -> dict[str, Any]:
    creator_meta = getattr(user, "creatormeta", None)
    profile_payload = {
        "inputs": {
            "platform": platform,
            "niche": (creator_meta.short_pitch if creator_meta else "") or "general",
            "follower_count": _safe_int(account.get("followers_count")),
            "engagement_rate_pct": _safe_float(summary_metrics.get("average_engagement_rate")),
            "posting_frequency": _safe_float(summary_metrics.get("post_frequency_weekly")),
            "audience_age_range": [row.get("label") for row in (audience.get("top_age_groups") or [])[:3]],
            "audience_gender_split": audience.get("gender_split") or {},
            "audience_concentration_pct": _safe_float(audience.get("percent_top_city_followers")),
            "conversion_rate_pct": _safe_float(summary_metrics.get("average_profile_visit_rate")),
            "save_rate_pct": _safe_float(summary_metrics.get("average_save_rate")),
            "past_brand_deals_count": _safe_int(summary_metrics.get("past_brand_deals_count")),
            "content_style_description": (creator_meta.bio if creator_meta else "") or account.get("biography") or "",
            "partnership_history": [],
            "reach": _safe_int(performance.get("reach")),
        }
    }

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return _fallback_feedback(profile_payload)

    try:
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": getattr(settings, "OPENAI_SOCIAL_ANALYZER_MODEL", "gpt-4.1-mini"),
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": "Return JSON only for creator profile evaluation."},
                    {"role": "user", "content": json.dumps({
                        "task": "Score creator profile health and return overall score, verdict, 4 dimension scores with diagnosis/actions, top priority actions, and benchmark comparison.",
                        "inputs": profile_payload["inputs"],
                    })},
                ],
                "response_format": {"type": "json_object"},
            },
            timeout=20,
        )
        response.raise_for_status()
        content = response.json().get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        parsed["inputs"] = profile_payload["inputs"]
        return parsed
    except Exception:
        return _fallback_feedback(profile_payload)
