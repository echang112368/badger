from .models import CreatorMeta


def creator_onboarding(request):
    if not request.user.is_authenticated or not request.user.is_creator:
        return {}
    meta, _ = CreatorMeta.objects.get_or_create(user=request.user)
    meta.refresh_onboarding_progress(persist=True)
    return {
        "creator_onboarding": meta.onboarding_status(),
    }
