from django.urls import path
from .views import LoginView, MeView, TokenRefreshView, logout_view

urlpatterns = [
    path("auth/login/", LoginView.as_view(), name="auth-login"),
    path("auth/refresh/", TokenRefreshView.as_view(), name="auth-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("logout/", logout_view, name="logout"),
]

# Example cURL
# Login with email
# curl -X POST http://localhost:8000/api/auth/login/ \
#   -H "Content-Type: application/json" \
#   -d '{"email":"user@demo.com","password":"demo123"}'
#
# Login with username
# curl -X POST http://localhost:8000/api/auth/login/ \
#   -H "Content-Type: application/json" \
#   -d '{"username":"demo","password":"demo123"}'
#
# Use access token to fetch profile
# curl http://localhost:8000/api/me/ \
#   -H "Authorization: Bearer <ACCESS>"
