from django.urls import path

from .api_views import CreatorOnboardingStatusView, CreatorOnboardingStepView

urlpatterns = [
    path("status/", CreatorOnboardingStatusView.as_view(), name="creator_onboarding_status"),
    path("<str:step>/", CreatorOnboardingStepView.as_view(), name="creator_onboarding_step"),
]
