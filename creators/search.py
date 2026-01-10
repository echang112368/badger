from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
import re
from difflib import SequenceMatcher
from typing import Iterable

from django.db.models import Q, Sum, Count
from django.utils import timezone

from collect.models import ReferralConversion
from creators.models import CreatorMeta
from merchants.models import MerchantItem, MerchantMeta


@dataclass(frozen=True)
class TimeWindow:
    start: timezone.datetime
    end: timezone.datetime


class SearchService:
    """Deterministic search service for intelligent, multi-model search.

    Example response structure:
    {
        "query": "cheap electronics creators",
        "intents": {
            "entities": ["creator", "item"],
            "time": {"label": "recent", "start": "...", "end": "..."},
            "numeric": {"min_price": 0, "max_price": 50, "ids": []},
            "performance": false
        },
        "results": {
            "creators": [{"id": 1, "label": "Jane Doe", "score": 125, "reasons": []}],
            "merchants": [],
            "items": [],
            "orders": [],
            "affiliates": []
        }
    }
    """

    ENTITY_KEYWORDS = {
        "creator": {"creator", "creators", "affiliate", "affiliates"},
        "merchant": {
            "merchant",
            "merchants",
            "business",
            "businesses",
            "brand",
            "brands",
            "company",
            "companies",
        },
        "item": {"product", "products", "item", "items", "catalog"},
        "order": {"order", "orders", "conversion", "conversions"},
    }
    PERFORMANCE_KEYWORDS = {"top", "best", "highest", "most", "performing"}
    TIME_KEYWORDS = {
        "today": "today",
        "yesterday": "yesterday",
        "last week": "last_week",
        "last month": "last_month",
        "recent": "recent",
    }
    STOP_WORDS = {
        "the",
        "a",
        "an",
        "for",
        "to",
        "in",
        "on",
        "with",
        "and",
        "of",
        "by",
        "at",
        "is",
    }
    DEFAULT_RESULTS_LIMIT = 20

    def __init__(self, query: str, user=None):
        self.query = (query or "").strip()
        self.user = user
        self.normalized_query = self.query.lower()

    def search(self) -> dict:
        intents = self.detect_intents()
        search_terms = self.extract_search_terms(intents)

        results = {
            "creators": [],
            "merchants": [],
            "items": [],
            "orders": [],
            "affiliates": [],
        }

        if self.should_search_entity("creator", intents):
            results["creators"] = self.search_creators(search_terms)

        if self.should_search_entity("merchant", intents):
            results["merchants"] = self.search_merchants(search_terms)

        if self.should_search_entity("item", intents):
            results["items"] = self.search_items(search_terms, intents)

        if self.should_search_entity("order", intents):
            results["orders"] = self.search_orders(search_terms, intents)

        if intents["performance"] and self.should_search_entity("creator", intents):
            results["affiliates"] = self.search_top_affiliates(intents)

        return {
            "query": self.query,
            "intents": intents,
            "results": results,
        }

    def detect_intents(self) -> dict:
        entities = self.detect_entity_intents()
        time_intent = self.detect_time_intent()
        numeric_intent = self.detect_numeric_intent()

        performance = any(
            keyword in self.normalized_query for keyword in self.PERFORMANCE_KEYWORDS
        )

        return {
            "entities": sorted(entities),
            "time": time_intent,
            "numeric": numeric_intent,
            "performance": performance,
        }

    def detect_entity_intents(self) -> set[str]:
        tokens = self.tokenize(self.normalized_query)
        intents = set()
        for entity, keywords in self.ENTITY_KEYWORDS.items():
            if keywords.intersection(tokens):
                intents.add(entity)
        return intents

    def detect_time_intent(self) -> dict | None:
        for phrase, label in self.TIME_KEYWORDS.items():
            if phrase in self.normalized_query:
                window = self.build_time_window(label)
                return {
                    "label": label,
                    "start": window.start.isoformat(),
                    "end": window.end.isoformat(),
                }
        return None

    def build_time_window(self, label: str) -> TimeWindow:
        now = timezone.now()
        if label == "today":
            start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            end = now
        elif label == "yesterday":
            start = (now - timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(days=1)
        elif label == "last_week":
            start = now - timedelta(days=7)
            end = now
        elif label == "last_month":
            start = now - timedelta(days=30)
            end = now
        else:
            start = now - timedelta(days=14)
            end = now
        return TimeWindow(start=start, end=end)

    def detect_numeric_intent(self) -> dict:
        ids = set()
        min_price = None
        max_price = None

        uuid_matches = re.findall(
            r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
            self.normalized_query,
        )
        ids.update(uuid_matches)

        id_matches = re.findall(r"\b(?:id|order)\s*(\d+)\b", self.normalized_query)
        ids.update(id_matches)

        max_match = re.search(
            r"\b(?:under|below|less than)\s*\$?([0-9]+(?:\.[0-9]+)?)\b",
            self.normalized_query,
        )
        if max_match:
            max_price = Decimal(max_match.group(1))

        min_match = re.search(
            r"\b(?:over|above|more than|at least)\s*\$?([0-9]+(?:\.[0-9]+)?)\b",
            self.normalized_query,
        )
        if min_match:
            min_price = Decimal(min_match.group(1))

        if "cheap" in self.normalized_query and max_price is None:
            max_price = Decimal("50")

        return {
            "ids": sorted(ids),
            "min_price": float(min_price) if min_price is not None else None,
            "max_price": float(max_price) if max_price is not None else None,
        }

    def extract_search_terms(self, intents: dict) -> list[str]:
        tokens = [token for token in self.tokenize(self.normalized_query)]
        intent_tokens = set().union(*self.ENTITY_KEYWORDS.values())
        intent_tokens.update(self.PERFORMANCE_KEYWORDS)
        intent_tokens.update(self.TIME_KEYWORDS.keys())
        intent_tokens.update(self.STOP_WORDS)
        return [token for token in tokens if token not in intent_tokens]

    def tokenize(self, text: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", text)

    def should_search_entity(self, entity: str, intents: dict) -> bool:
        entities = intents["entities"]
        return not entities or entity in entities

    def search_creators(self, terms: Iterable[str]) -> list[dict]:
        if not terms:
            return []
        qs = CreatorMeta.objects.select_related("user").filter(
            self.build_multi_term_query(
                terms,
                [
                    "user__username",
                    "user__first_name",
                    "user__last_name",
                    "bio",
                    "short_pitch",
                ],
            )
        )
        return self.score_results(
            qs,
            terms,
            label_func=lambda creator: creator.user.get_full_name()
            or creator.user.username,
            match_fields=(
                "user__username",
                "user__first_name",
                "user__last_name",
                "bio",
                "short_pitch",
            ),
            extra_payload=lambda creator: {
                "uuid": str(creator.uuid),
                "marketplace_enabled": creator.marketplace_enabled,
            },
        )

    def search_merchants(self, terms: Iterable[str]) -> list[dict]:
        if not terms:
            return []
        qs = MerchantMeta.objects.select_related("user").filter(
            self.build_multi_term_query(
                terms,
                ["company_name", "user__username", "shopify_store_domain"],
            )
        )
        return self.score_results(
            qs,
            terms,
            label_func=lambda merchant: merchant.company_name
            or merchant.user.username,
            match_fields=("company_name", "user__username", "shopify_store_domain"),
            extra_payload=lambda merchant: {
                "uuid": str(merchant.uuid),
                "business_type": merchant.business_type,
                "marketplace_enabled": merchant.marketplace_enabled,
            },
        )

    def search_items(self, terms: Iterable[str], intents: dict) -> list[dict]:
        if not terms:
            return []
        qs = MerchantItem.objects.select_related("merchant")

        numeric = intents.get("numeric") or {}
        min_price = numeric.get("min_price")
        max_price = numeric.get("max_price")
        if min_price is not None:
            qs = qs.filter(price__gte=min_price)
        if max_price is not None:
            qs = qs.filter(price__lte=max_price)

        qs = qs.filter(self.build_multi_term_query(terms, ["title"]))

        return self.score_results(
            qs,
            terms,
            label_func=lambda item: item.title,
            match_fields=("title",),
            extra_payload=lambda item: {
                "merchant_id": item.merchant_id,
                "price": float(item.price) if item.price is not None else None,
                "link": item.link,
            },
        )

    def search_orders(self, terms: Iterable[str], intents: dict) -> list[dict]:
        qs = ReferralConversion.objects.select_related("creator", "merchant")
        numeric = intents.get("numeric") or {}
        ids = numeric.get("ids") or []
        if ids:
            qs = qs.filter(Q(order_id__in=ids) | Q(order_id__icontains=ids[0]))
        if terms:
            qs = qs.filter(
                self.build_multi_term_query(
                    terms,
                    ["order_id", "creator__username", "merchant__username"],
                )
            )

        time_intent = intents.get("time")
        if time_intent:
            window = self.build_time_window(time_intent["label"])
            qs = qs.filter(created_at__gte=window.start, created_at__lte=window.end)

        return self.score_results(
            qs,
            terms,
            label_func=lambda order: order.order_id or "(unlabeled order)",
            match_fields=("order_id", "creator__username", "merchant__username"),
            extra_payload=lambda order: {
                "order_amount": float(order.order_amount),
                "commission_amount": float(order.commission_amount),
                "created_at": order.created_at.isoformat(),
            },
        )

    def search_top_affiliates(self, intents: dict) -> list[dict]:
        qs = ReferralConversion.objects.select_related("creator")
        time_intent = intents.get("time")
        if time_intent:
            window = self.build_time_window(time_intent["label"])
            qs = qs.filter(created_at__gte=window.start, created_at__lte=window.end)

        top_creators = (
            qs.values("creator")
            .annotate(conversions=Count("id"), revenue=Sum("order_amount"))
            .order_by("-revenue", "-conversions")[:10]
        )

        creator_ids = [row["creator"] for row in top_creators if row["creator"]]
        creators = {
            creator.pk: creator
            for creator in CreatorMeta.objects.select_related("user").filter(
                user_id__in=creator_ids
            )
        }

        results = []
        for row in top_creators:
            creator = creators.get(row["creator"])
            if not creator:
                continue
            results.append(
                {
                    "id": creator.pk,
                    "label": creator.user.get_full_name() or creator.user.username,
                    "score": 100 + int(row["revenue"] or 0),
                    "reasons": ["performance intent"],
                    "metadata": {
                        "uuid": str(creator.uuid),
                        "conversions": row["conversions"],
                        "revenue": float(row["revenue"] or 0),
                    },
                }
            )
        return results

    def build_multi_term_query(self, terms: Iterable[str], fields: Iterable[str]) -> Q:
        query = Q()
        for term in terms:
            term_query = Q()
            for field in fields:
                term_query |= Q(**{f"{field}__icontains": term})
            query &= term_query
        return query

    def score_results(
        self,
        queryset,
        terms: Iterable[str],
        label_func,
        match_fields: Iterable[str],
        extra_payload,
    ) -> list[dict]:
        results = []
        for obj in queryset[: self.DEFAULT_RESULTS_LIMIT]:
            label = label_func(obj)
            score, reasons = self.score_object(obj, label, terms, match_fields)
            results.append(
                {
                    "id": obj.pk,
                    "label": label,
                    "score": score,
                    "reasons": reasons,
                    "metadata": extra_payload(obj),
                }
            )
        return sorted(results, key=lambda item: item["score"], reverse=True)

    def score_object(
        self, obj, label: str, terms: Iterable[str], match_fields: Iterable[str]
    ) -> tuple[int, list[str]]:
        score = 0
        reasons = []
        for term in terms:
            term_score, term_reason = self.score_term_against_fields(
                term, obj, match_fields
            )
            score += term_score
            if term_reason:
                reasons.append(term_reason)
        if self.is_exact_match(label, self.query):
            score += 50
            reasons.append("exact label match")
        return score, reasons

    def score_term_against_fields(
        self, term: str, obj, fields: Iterable[str]
    ) -> tuple[int, str | None]:
        best_score = 0
        best_reason = None
        for field in fields:
            value = self.resolve_attr(obj, field)
            if not value:
                continue
            value_text = str(value).lower()
            term_score = self.score_text_match(value_text, term)
            if term_score > best_score:
                best_score = term_score
                best_reason = f"matched {field.replace('__', ' ')}"
        return best_score, best_reason

    def score_text_match(self, value_text: str, term: str) -> int:
        if value_text == term:
            return 100
        if value_text.startswith(term):
            return 70
        if term in value_text:
            return 50
        similarity = SequenceMatcher(None, value_text, term).ratio()
        if similarity >= 0.85:
            return 30
        return 0

    def resolve_attr(self, obj, dotted_path: str):
        value = obj
        for part in dotted_path.split("__"):
            value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def is_exact_match(self, value: str, query: str) -> bool:
        return value.strip().lower() == query.strip().lower()

    def log_query(self, intents: dict) -> None:
        """Hook for logging queries in analytics."""
        return None

    def get_embedding_hook(self, query: str) -> None:
        """Placeholder for future semantic embeddings (no-op for now)."""
        return None
