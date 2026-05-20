from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MatchResult:
    score: int
    reasoning: str
    highlights: list[str]
    source: str


@dataclass
class MatchConfig:
    model: str = "gpt-4.1-mini"
    max_ai_candidates: int = 25
    min_rule_score_for_ai: int = 0


CreatorCard = dict[str, Any]
BusinessContext = dict[str, Any]
