"""Exception classes for the local ``rest_framework_simplejwt`` subset."""


class TokenError(Exception):
    """Raised when a token cannot be decoded or validated."""

    default_detail = "Token is invalid or has expired."

    def __init__(self, detail=None):
        super().__init__(detail or self.default_detail)
        self.detail = detail or self.default_detail
