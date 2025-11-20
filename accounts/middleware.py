"""Middleware enforcing email verification for authenticated users."""

from django.shortcuts import redirect
from django.urls import resolve, reverse


class EmailVerificationRequiredMiddleware:
    """Redirect authenticated users without verified email to verification flow."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.user.is_authenticated and not getattr(request.user, "email_verified", True):
            try:
                resolver_match = resolve(request.path)
                view_name = resolver_match.view_name
            except Exception:
                view_name = None

            allowed_names = {
                "verify_email",
                "resend_verification",
                "logout",
                "password_reset",
                "password_reset_done",
                "password_reset_confirm",
                "password_reset_complete",
                "admin:logout",
            }

            if view_name not in allowed_names and not request.path.startswith(
                reverse("admin:index")
            ):
                return redirect("verify_email")

        return self.get_response(request)
