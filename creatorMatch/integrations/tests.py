from django.test import TestCase
from unittest.mock import patch

from creatorMatch.services.matching.orchestrator import apply_matching_scores


class OrchestratorCachingTests(TestCase):
    def setUp(self):
        self.cards = [
            {
                "creator_id": 1,
                "name": "Creator A",
                "handle": "@a",
                "platform": "instagram",
                "niche": "beauty",
                "niche_text": "beauty skincare",
                "followers_count": 12000,
                "engagement_rate": 4.1,
                "average_reach": 8000,
                "audience_location": "United States",
                "profile_views": 1000,
                "website_clicks": 250,
                "average_save_rate": 1.7,
                "average_share_rate": 0.9,
                "average_comment_rate": 0.5,
                "fallback_match_score": 78,
            }
        ]
        self.business_context = {"campaign_goal": "conversions_sales", "brand_tone": "professional"}

    @patch("creatorMatch.services.matching.orchestrator.score_candidates_with_ai_diagnostics")
    def test_reuses_ai_scores_when_inputs_have_not_changed(self, mock_ai):
        mock_ai.return_value = (
            {1: {"score": 91, "reasoning": "Strong fit.", "creator_summary": "Great fit", "highlights": ["high engagement"]}},
            {"ai_attempted": True, "error_code": None, "error_message": None, "response_id": "resp_123"},
        )

        first = apply_matching_scores(list(self.cards), dict(self.business_context))
        second = apply_matching_scores(list(self.cards), dict(self.business_context))

        self.assertEqual(mock_ai.call_count, 1)
        self.assertEqual(first[0]["match_score"], 91)
        self.assertEqual(second[0]["match_score"], 91)
        self.assertFalse(first[0]["match_diagnostics"]["ai_cache_hit"])
        self.assertTrue(second[0]["match_diagnostics"]["ai_cache_hit"])

# Create your tests here.
