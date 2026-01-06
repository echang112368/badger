from django.urls import path

from .api_views import (
    CreatorNameView,
    CreatorOnboardingStatusView,
    CreatorOnboardingStepView,
)

urlpatterns = [
    path('onboarding/status/', CreatorOnboardingStatusView.as_view(), name='creator_onboarding_status'),
    path('onboarding/<str:step>/', CreatorOnboardingStepView.as_view(), name='creator_onboarding_step'),
    path('<uuid:uuid>/', CreatorNameView.as_view(), name='creator_name_api'),
]
