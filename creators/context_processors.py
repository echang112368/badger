from .models import CreatorMeta
from ledger.models import LedgerEntry


def creator_onboarding(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_creator:
        return {}

    meta, _ = CreatorMeta.objects.get_or_create(
        user=user,
        defaults={"display_name": user.username},
    )

    status = meta.refresh_onboarding_status(save=False)
    steps = {
        "identity": meta.identity_complete(),
        "platforms": meta.platforms_complete(),
        "content": meta.content_complete(),
        "performance": meta.performance_complete(),
        "payouts": meta.payouts_complete(),
    }

    performance_available = LedgerEntry.objects.filter(creator=user).exists()
    performance_message = (
        "Performance signals are now available from your first sale."
        if performance_available
        else "We'll add this automatically after your first sale."
    )

    return {
        "creator_onboarding": {
            "current_step": meta.onboarding_step,
            "completion_percent": status["completion_percent"],
            "onboarding_completed": status["onboarding_completed"],
            "next_recommended_step": status["next_step"],
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
    }
