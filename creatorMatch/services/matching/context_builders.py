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
            "performance_priority": getattr(preferences, "performance_priority", ""),
            "preferred_creator_style": list(getattr(preferences, "preferred_creator_style", []) or []),
            "brand_description": getattr(preferences, "brand_description", ""),
            "target_customer_profile": getattr(preferences, "target_customer_profile", ""),
            "content_messaging_direction": getattr(preferences, "content_messaging_direction", ""),
            "content_to_avoid": getattr(preferences, "content_to_avoid", ""),
            "competitor_or_conflict_notes": getattr(preferences, "competitor_or_conflict_notes", ""),
            "example_creators_or_brands": getattr(preferences, "example_creators_or_brands", ""),
        },
    }
