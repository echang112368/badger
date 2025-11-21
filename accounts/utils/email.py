"""Email utilities for the accounts app."""

from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string


def send_verification_email(user, verification_code: str) -> None:
    """Send a verification code to the user's email address."""

    context = {"user": user, "verification_code": verification_code}
    subject = "Verify your email address"
    message = render_to_string("accounts/emails/verification_email.txt", context)
    html_message = render_to_string("accounts/emails/verification_email.html", context)

    print(message)
    
    send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_message,
    )
