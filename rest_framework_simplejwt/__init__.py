"""Minimal subset of the ``rest_framework_simplejwt`` package used in tests.

This lightweight implementation provides the small collection of classes that the
project relies on (token helpers, authentication class, and token views).  It is
*not* a drop-in replacement for the third-party package, but it supports the
limited behaviour that the codebase expects during the exercises.
"""

from .tokens import RefreshToken, AccessToken
from .exceptions import TokenError

__all__ = [
    "AccessToken",
    "RefreshToken",
    "TokenError",
]
