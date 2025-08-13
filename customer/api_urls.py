from django.urls import path

from .api_views import LoginView

urlpatterns = [
    path("login/", LoginView.as_view(), name="api_login"),
]

