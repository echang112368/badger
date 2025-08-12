from django.urls import path
from .views import APILoginView, MeView, TokenRefreshView, logout_view

urlpatterns = [
    path("auth/login/", APILoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("logout/", logout_view, name="logout"),
]
