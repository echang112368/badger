"""Email utilities for the accounts app."""

from django.conf import settings
from django.core.mail import send_mail


def send_verification_email(user, verification_code: str) -> None:
    """Send a verification code to the user's email address."""

    subject = "Your verification code"
    message = f"Your verification code is: {verification_code}"

    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
    )
