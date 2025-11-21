"""Email verification helpers."""

import random
from datetime import timedelta
from typing import Optional

from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts.utils.email import send_verification_email

User = get_user_model()


def generate_verification_code(length: int = 6) -> str:
    """Return a numeric verification code of the given length."""

    digits = "0123456789"
    return "".join(random.choice(digits) for _ in range(length))


def ensure_verification_code(user: User, regenerate: bool = False) -> str:
    """Guarantee the user has a verification code and return it."""

    if regenerate or not user.verification_code:
        user.verification_code = generate_verification_code()
        user.email_verified = False
        user.save(update_fields=["verification_code", "email_verified"])
    return user.verification_code or ""


def send_user_verification_email(user: User, regenerate: bool = False) -> str:
    """Generate (if needed) and send a verification code to the user."""

    code = ensure_verification_code(user, regenerate=regenerate)
    send_verification_email(user, code)
    return code


def verify_user_code(user: User, submitted_code: str) -> bool:
    """Validate and apply a verification code for the user."""

    cleaned_code = (submitted_code or "").strip()
    if not cleaned_code or cleaned_code != (user.verification_code or ""):
        return False

    user.email_verified = True
    user.verification_code = None
    user.save(update_fields=["email_verified", "verification_code"])
    return True


def get_user_by_pk(user_id: Optional[int]) -> Optional[User]:
    """Safely fetch a user by primary key."""

    if not user_id:
        return None
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return None


def needs_email_verification(user: User) -> bool:
    """Determine whether an unverified user must verify their email now."""

    if user.email_verified:
        return False

    cutoff = timezone.now() - timedelta(days=7)
    return user.last_login is None or user.last_login <= cutoff
