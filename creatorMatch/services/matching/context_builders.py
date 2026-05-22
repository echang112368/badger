from __future__ import annotations

from typing import Any

from merchants.models import CompanyCreatorPreferences


def build_business_context(preferences: CompanyCreatorPreferences | None, filters: Any) -> dict[str, Any]:
    return {
        "filters": {
            "platform": filters.platform,
            "niche": filters.niche,
            "audience_gender": filters.audience_gender,
            "audience_age": filters.audience_age,
            "audience_location": filters.audience_location,
            "follower_min": filters.follower_min,
            "follower_max": filters.follower_max,
            "min_engagement_rate": filters.min_engagement_rate,
        },
        "preferences": {
            "campaign_goal": getattr(preferences, "campaign_goal", ""),
            "campaign_stage": getattr(preferences, "campaign_stage", ""),
            "performance_priority": getattr(preferences, "performance_priority", ""),
            "preferred_creator_style": list(getattr(preferences, "preferred_creator_style", []) or []),
            "brand_tone": getattr(preferences, "brand_tone", ""),
            "content_deliverables": list(getattr(preferences, "content_deliverables", []) or []),
            "risk_tolerance": getattr(preferences, "risk_tolerance", ""),
            "budget_range": getattr(preferences, "budget_range", ""),
            "budget_min": getattr(preferences, "budget_min", None),
            "budget_max": getattr(preferences, "budget_max", None),
            "brand_tone_keywords": getattr(preferences, "brand_tone_keywords", ""),
            "target_customer_age_range": getattr(preferences, "target_customer_age_range", ""),
            "target_customer_gender_skew": getattr(preferences, "target_customer_gender_skew", ""),
            "target_customer_location": getattr(preferences, "target_customer_location", ""),
            "preferred_platforms": list(getattr(preferences, "preferred_platforms", []) or []),
            "minimum_engagement_rate": float(getattr(preferences, "minimum_engagement_rate", 0) or 0),
            "success_metric_priority": getattr(preferences, "success_metric_priority", ""),
            "has_run_influencer_campaigns_before": getattr(preferences, "has_run_influencer_campaigns_before", None),
            "past_campaign_learnings": getattr(preferences, "past_campaign_learnings", ""),
            "ideal_creator_description": getattr(preferences, "ideal_creator_description", ""),
            "brand_description": getattr(preferences, "brand_description", ""),
            "product_or_service_description": getattr(preferences, "product_or_service_description", ""),
            "campaign_success_definition": getattr(preferences, "campaign_success_definition", ""),
            "content_to_avoid": getattr(preferences, "content_to_avoid", ""),
            "competitor_or_conflict_notes": getattr(preferences, "competitor_or_conflict_notes", ""),
            "example_creators_or_brands": getattr(preferences, "example_creators_or_brands", ""),
        },
    }
