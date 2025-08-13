from django.urls import path

from .api_views import VerifyAccountView

urlpatterns = [
    path("verify/", VerifyAccountView.as_view(), name="api_verify"),
]

