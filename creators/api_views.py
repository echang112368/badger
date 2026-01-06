import logging

from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status
from django.shortcuts import get_object_or_404

from .models import CreatorMeta

logger = logging.getLogger(__name__)


class CreatorNameView(APIView):
    """Return the full name for a creator identified by UUID.

    Requires a valid JWT access token.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        meta = get_object_or_404(CreatorMeta, uuid=uuid)
        user = meta.user
        name = f"{user.first_name} {user.last_name}".strip() or user.username
        return Response({"name": name})


class CreatorOnboardingStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_creator:
            return Response({"detail": "Creator access required."}, status=status.HTTP_403_FORBIDDEN)
        meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
        meta.refresh_onboarding_progress(persist=True)
        return Response(meta.onboarding_status())


class CreatorOnboardingStepView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, step):
        if not request.user.is_creator:
            return Response({"detail": "Creator access required."}, status=status.HTTP_403_FORBIDDEN)
        if step not in {"identity", "platforms", "content", "performance", "payouts"}:
            return Response({"detail": "Unknown onboarding step."}, status=status.HTTP_400_BAD_REQUEST)

        meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
        if meta.onboarding_started_at is None:
            meta.onboarding_started_at = timezone.now()

        data = request.data or {}
        skip = bool(data.get("skip"))
        if skip and step in {"content", "performance"}:
            skipped = set(meta.onboarding_skipped_steps or [])
            skipped.add(step)
            meta.onboarding_skipped_steps = list(skipped)
            meta.save(update_fields=["onboarding_skipped_steps", "onboarding_started_at"])
            meta.refresh_onboarding_progress(persist=True)
            return Response(meta.onboarding_status())

        if step == "identity":
            display_name = str(data.get("display_name", "")).strip()
            country = str(data.get("country", "")).strip()
            primary_niches = data.get("primary_niches") or []
            if isinstance(primary_niches, str):
                primary_niches = [n.strip() for n in primary_niches.split(",") if n.strip()]
            if display_name and display_name != request.user.username:
                if type(request.user).objects.filter(username=display_name).exclude(pk=request.user.pk).exists():
                    return Response(
                        {"detail": "Display name is already taken."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                request.user.username = display_name
                request.user.save(update_fields=["username"])
            meta.country = country
            meta.primary_niches = primary_niches
            meta.save(update_fields=["country", "primary_niches", "onboarding_started_at"])

        if step == "platforms":
            platforms = data.get("platforms") or []
            if isinstance(platforms, dict):
                platforms = [platforms]
            normalized = []
            for entry in platforms:
                if not isinstance(entry, dict):
                    continue
                platform_type = str(entry.get("platform", "")).strip()
                url = str(entry.get("url", "")).strip()
                followers = str(entry.get("followers_range", "")).strip()
                if platform_type and url:
                    normalized.append(
                        {
                            "platform": platform_type,
                            "url": url,
                            "followers_range": followers,
                        }
                    )
            meta.platforms = normalized
            meta.save(update_fields=["platforms", "onboarding_started_at"])

        if step == "content":
            tags = data.get("content_style_tags") or []
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            posting_frequency = str(data.get("posting_frequency", "")).strip()
            open_to_gifting = data.get("open_to_gifting")
            if isinstance(open_to_gifting, str):
                open_to_gifting = open_to_gifting.lower() in {"true", "1", "yes", "on"}
            meta.content_style_tags = tags
            meta.posting_frequency = posting_frequency
            meta.open_to_gifting = open_to_gifting
            meta.save(
                update_fields=[
                    "content_style_tags",
                    "posting_frequency",
                    "open_to_gifting",
                    "onboarding_started_at",
                ]
            )

        if step == "performance":
            meta.save(update_fields=["onboarding_started_at"])

        if step == "payouts":
            payout_method = str(data.get("payout_method", "")).strip()
            paypal_email = str(data.get("paypal_email", "")).strip()
            tax_info = str(data.get("tax_info", "")).strip()
            meta.payout_method = payout_method
            meta.paypal_email = paypal_email
            meta.tax_info = tax_info
            meta.save(
                update_fields=[
                    "payout_method",
                    "paypal_email",
                    "tax_info",
                    "onboarding_started_at",
                ]
            )

        before_completion = meta.onboarding_completed
        meta.refresh_onboarding_progress(persist=True)
        status_payload = meta.onboarding_status()

        if meta.onboarding_completed and not before_completion:
            duration = None
            if meta.onboarding_started_at and meta.onboarding_completed_at:
                duration = (meta.onboarding_completed_at - meta.onboarding_started_at).total_seconds()
            logger.info(
                "CREATOR_ONBOARDING_COMPLETED",
                extra={
                    "creator_id": request.user.id,
                    "duration_seconds": duration,
                },
            )

        step_status = next(
            (entry for entry in status_payload["steps"] if entry["step"] == step), None
        )
        if step_status and step_status["completed"]:
            logger.info(
                "CREATOR_ONBOARDING_STEP_COMPLETED",
                extra={"creator_id": request.user.id, "step": step},
            )

        return Response(status_payload)
