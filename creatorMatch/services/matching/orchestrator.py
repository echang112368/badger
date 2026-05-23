from __future__ import annotations

from creatorMatch.services.matching.strategies.openai import score_candidates_with_ai_diagnostics
from creatorMatch.services.matching.types import MatchConfig


def apply_matching_scores(
    cards: list[dict],
    business_context: dict,
    fallback_score_key: str = "fallback_match_score",
    config: MatchConfig | None = None,
) -> list[dict]:
    config = config or MatchConfig()

    ai_pool = [
        card
        for card in sorted(cards, key=lambda row: row.get(fallback_score_key, 0), reverse=True)
        if (card.get(fallback_score_key) or 0) >= config.min_rule_score_for_ai
    ][: config.max_ai_candidates]

    ai_payload = [
        {
            "creator_id": card.get("creator_id"),
            "name": card.get("name"),
            "handle": card.get("handle"),
            "platform": card.get("platform"),
            "niche": card.get("niche"),
            "niche_text": card.get("niche_text"),
            "followers_count": card.get("followers_count"),
            "engagement_rate": card.get("engagement_rate"),
            "average_reach": card.get("average_reach"),
            "audience_location": card.get("audience_location"),
            "profile_views": card.get("profile_views"),
            "website_clicks": card.get("website_clicks"),
            "average_save_rate": card.get("average_save_rate"),
            "average_share_rate": card.get("average_share_rate"),
            "average_comment_rate": card.get("average_comment_rate"),
        }
        for card in ai_pool
    ]
    ai_by_creator_id, ai_call_diagnostics = score_candidates_with_ai_diagnostics(
        ai_payload,
        business_context,
        config=config,
    )
    ai_candidate_ids = {int(row.get("creator_id") or 0) for row in ai_payload}
    ai_scored_count = 0

    for card in cards:
        fallback_score = card.get(fallback_score_key) or 0
        ai_result = ai_by_creator_id.get(int(card.get("creator_id") or 0))
        card["match_score"] = ai_result["score"] if ai_result else fallback_score
        card["match_reasoning"] = ai_result["reasoning"] if ai_result else "Rule-based fallback score."
        card["match_creator_summary"] = (
            ai_result.get("creator_summary")
            if ai_result and ai_result.get("creator_summary")
            else "Summary unavailable. Using rule-based fallback profile scoring."
        )
        card["match_highlights"] = ai_result["highlights"] if ai_result else []
        card["match_source"] = "ai" if ai_result else "rules"
        card["match_ai_candidate"] = int(card.get("creator_id") or 0) in ai_candidate_ids
        if ai_result:
            ai_scored_count += 1

    diagnostics = {
        "ai_candidates_count": len(ai_payload),
        "ai_scored_count": ai_scored_count,
        "ai_pool_score_threshold": config.min_rule_score_for_ai,
        "ai_pool_cap": config.max_ai_candidates,
        "ai_attempted": bool(ai_call_diagnostics.get("ai_attempted")),
        "ai_error_code": ai_call_diagnostics.get("error_code"),
        "ai_error_message": ai_call_diagnostics.get("error_message"),
        "ai_response_id": ai_call_diagnostics.get("response_id"),
    }
    for card in cards:
        card["match_diagnostics"] = diagnostics

    return cards
