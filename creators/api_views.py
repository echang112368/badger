import logging

from django.shortcuts import get_object_or_404
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ledger.models import LedgerEntry

from .models import CreatorMeta, OnboardingStep


logger = logging.getLogger(__name__)


class CreatorNameView(APIView):
    """Return the full name for a creator identified by UUID.

    Requires a valid JWT access token.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        meta = get_object_or_404(CreatorMeta, uuid=uuid)
        user = meta.user
        name = f"{user.first_name} {user.last_name}".strip()
        return Response({"name": name})


def _detect_platform(url: str) -> str:
    if not url:
        return ""
    lowered = url.lower()
    if "tiktok" in lowered:
        return "TikTok"
    if "instagram" in lowered:
        return "Instagram"
    if "youtube" in lowered or "youtu.be" in lowered:
        return "YouTube"
    if "twitch" in lowered:
        return "Twitch"
    if "facebook" in lowered:
        return "Facebook"
    if "twitter" in lowered or "x.com" in lowered:
        return "X"
    if "pinterest" in lowered:
        return "Pinterest"
    return ""


def _normalize_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _normalize_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "on"}
    if value is None:
        return None
    return bool(value)


def _step_complete(meta: CreatorMeta, step: str) -> bool:
    if step == OnboardingStep.IDENTITY:
        return meta.identity_complete()
    if step == OnboardingStep.PLATFORMS:
        return meta.platforms_complete()
    if step == OnboardingStep.CONTENT:
        return meta.content_complete()
    if step == OnboardingStep.PERFORMANCE:
        return meta.performance_complete()
    if step == OnboardingStep.PAYOUTS:
        return meta.payouts_complete()
    return False


class CreatorOnboardingStatusView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        if not request.user.is_creator:
            return Response({"detail": "Creator access required."}, status=403)
        meta, _ = CreatorMeta.objects.get_or_create(
            user=request.user,
            defaults={"display_name": request.user.username},
        )
        status = meta.refresh_onboarding_status(save=False)
        steps = {
            "identity": meta.identity_complete(),
            "platforms": meta.platforms_complete(),
            "content": meta.content_complete(),
            "performance": meta.performance_complete(),
            "payouts": meta.payouts_complete(),
        }
        performance_available = LedgerEntry.objects.filter(creator=request.user).exists()
        performance_message = (
            "Performance signals are now available from your first sale."
            if performance_available
            else "We'll add this automatically after your first sale."
        )
        return Response(
            {
                "current_step": meta.onboarding_step,
                "completion_percent": status["completion_percent"],
                "next_recommended_step": status["next_step"],
                "onboarding_completed": status["onboarding_completed"],
                "steps": steps,
                "profile": {
                    "display_name": meta.display_name,
                    "country": meta.country,
                    "primary_niches": meta.primary_niches,
                    "platforms": meta.platforms,
                    "content_style_tags": meta.content_style_tags,
                    "posting_frequency": meta.posting_frequency,
                    "open_to_gifting": meta.open_to_gifting,
                    "payout_method": meta.payout_method,
                    "paypal_email": meta.paypal_email,
                    "tax_info_submitted": meta.tax_info_submitted,
                },
                "performance_available": performance_available,
                "performance_message": performance_message,
            }
        )


class CreatorOnboardingStepView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, step):
        if not request.user.is_creator:
            return Response({"detail": "Creator access required."}, status=403)
        meta, _ = CreatorMeta.objects.get_or_create(
            user=request.user,
            defaults={"display_name": request.user.username},
        )
        payload = request.data or {}
        step = step.lower()
        valid_steps = {
            OnboardingStep.IDENTITY,
            OnboardingStep.PLATFORMS,
            OnboardingStep.CONTENT,
            OnboardingStep.PERFORMANCE,
            OnboardingStep.PAYOUTS,
        }
        if step not in valid_steps:
            return Response({"detail": "Invalid onboarding step."}, status=400)
        before_complete = _step_complete(meta, step)

        if step == OnboardingStep.IDENTITY:
            display_name = (payload.get("display_name") or "").strip()
            country = (payload.get("country") or "").strip()
            niches = _normalize_list(payload.get("primary_niches"))
            if display_name:
                meta.display_name = display_name
            if country:
                meta.country = country
            if niches:
                meta.primary_niches = niches

        elif step == OnboardingStep.PLATFORMS:
            platforms_payload = payload.get("platforms")
            platforms = []
            if isinstance(platforms_payload, list):
                for platform in platforms_payload:
                    if not isinstance(platform, dict):
                        continue
                    url = (platform.get("url") or "").strip()
                    if not url:
                        continue
                    platform_type = (platform.get("type") or "").strip()
                    followers = (platform.get("followers") or "").strip()
                    if not platform_type:
                        platform_type = _detect_platform(url)
                    platforms.append(
                        {"type": platform_type, "url": url, "followers": followers}
                    )
            else:
                url = (payload.get("profile_url") or "").strip()
                platform_type = (payload.get("platform_type") or "").strip()
                followers = (payload.get("followers") or "").strip()
                if url:
                    if not platform_type:
                        platform_type = _detect_platform(url)
                    platforms.append(
                        {"type": platform_type, "url": url, "followers": followers}
                    )
            if platforms:
                meta.platforms = platforms

        elif step == OnboardingStep.CONTENT:
            if payload.get("skip"):
                meta.onboarding_content_skipped = True
            else:
                meta.onboarding_content_skipped = False
                tags = _normalize_list(payload.get("content_style_tags"))
                posting_frequency = (payload.get("posting_frequency") or "").strip()
                open_to_gifting = _normalize_bool(payload.get("open_to_gifting"))
                if tags:
                    meta.content_style_tags = tags
                if posting_frequency:
                    meta.posting_frequency = posting_frequency
                if open_to_gifting is not None:
                    meta.open_to_gifting = bool(open_to_gifting)

        elif step == OnboardingStep.PERFORMANCE:
            meta.onboarding_performance_skipped = True

        elif step == OnboardingStep.PAYOUTS:
            payout_method = (payload.get("payout_method") or "").strip()
            paypal_email = (payload.get("paypal_email") or "").strip()
            tax_info_submitted = _normalize_bool(payload.get("tax_info_submitted"))
            if payout_method:
                meta.payout_method = payout_method
            if paypal_email:
                meta.paypal_email = paypal_email
            if tax_info_submitted is not None:
                meta.tax_info_submitted = tax_info_submitted

        meta.save()
        status = meta.refresh_onboarding_status(save=True)

        after_complete = _step_complete(meta, step)
        if after_complete and not before_complete:
            logger.info(
                "CREATOR_ONBOARDING_STEP_COMPLETED",
                extra={"creator_id": meta.user_id, "step": step},
            )

        performance_available = LedgerEntry.objects.filter(creator=request.user).exists()
        performance_message = (
            "Performance signals are now available from your first sale."
            if performance_available
            else "We'll add this automatically after your first sale."
        )
        return Response(
            {
                "current_step": meta.onboarding_step,
                "completion_percent": status["completion_percent"],
                "next_recommended_step": status["next_step"],
                "onboarding_completed": status["onboarding_completed"],
                "steps": {
                    "identity": meta.identity_complete(),
                    "platforms": meta.platforms_complete(),
                    "content": meta.content_complete(),
                    "performance": meta.performance_complete(),
                    "payouts": meta.payouts_complete(),
                },
                "profile": {
                    "display_name": meta.display_name,
                    "country": meta.country,
                    "primary_niches": meta.primary_niches,
                    "platforms": meta.platforms,
                    "content_style_tags": meta.content_style_tags,
                    "posting_frequency": meta.posting_frequency,
                    "open_to_gifting": meta.open_to_gifting,
                    "payout_method": meta.payout_method,
                    "paypal_email": meta.paypal_email,
                    "tax_info_submitted": meta.tax_info_submitted,
                },
                "performance_available": performance_available,
                "performance_message": performance_message,
            }
        )
