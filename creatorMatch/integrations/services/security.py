import logging

from django.conf import settings
from django.core import signing

logger = logging.getLogger(__name__)

_TOKEN_SIGNER_SALT = "creatorMatch.integrations.tokens"


def encrypt_token(raw_token: str) -> str:
    """
    Signed serialization for token-at-rest protection.

    For stronger encryption-at-rest, replace with a KMS-backed encrypt/decrypt
    implementation or django-fernet-fields.
    """
    if not raw_token:
        return ""
    return signing.dumps(raw_token, salt=_TOKEN_SIGNER_SALT)


def decrypt_token(serialized_token: str) -> str:
    if not serialized_token:
        return ""
    max_age = getattr(settings, "INTEGRATIONS_TOKEN_MAX_AGE_SECONDS", None)
    return signing.loads(serialized_token, salt=_TOKEN_SIGNER_SALT, max_age=max_age)
