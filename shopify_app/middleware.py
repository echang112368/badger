"""Custom middleware for Shopify embedded app integration."""

from __future__ import annotations

import logging
from django.conf import settings
from django.http import JsonResponse

from .oauth import ShopifyOAuthError, normalise_shop_domain, verify_session_token

logger = logging.getLogger(__name__)


class ShopifyEmbeddedAppSecurityMiddleware:
    """Adjust security headers so the app can render inside Shopify Admin."""

    def __init__(self, get_response):
        self.get_response = get_response
        default_ancestors = (
            "https://admin.shopify.com",
            "https://*.myshopify.com",
        )
        frame_ancestors = getattr(
            settings,
            "SHOPIFY_EMBEDDED_APP_FRAME_ANCESTORS",
            default_ancestors,
        )
        if not frame_ancestors:
            frame_ancestors = default_ancestors
        self.frame_ancestors = tuple(frame_ancestors)

    def __call__(self, request):
        response = self.get_response(request)

        frame_ancestors_directive = "frame-ancestors " + " ".join(self.frame_ancestors)
        existing_csp = response.headers.get("Content-Security-Policy")

        if existing_csp:
            directives = [
                directive.strip()
                for directive in existing_csp.split(";")
                if directive.strip() and not directive.strip().startswith("frame-ancestors")
            ]
            directives.append(frame_ancestors_directive)
            response.headers["Content-Security-Policy"] = "; ".join(directives)
        else:
            response.headers["Content-Security-Policy"] = frame_ancestors_directive

        response.headers["X-Frame-Options"] = "ALLOWALL"
        return response


class ShopifySessionTokenMiddleware:
    """Validate App Bridge session tokens on every request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def _build_error(self, reason: str, status_code: int = 401):
        logger.error(
            "SESSION_TOKEN_INVALID",
            extra={"reason": reason},
        )
        return JsonResponse({"error": reason}, status=status_code)

    def __call__(self, request):
        auth_header = request.headers.get("Authorization") or ""
        if not auth_header.startswith("Bearer "):
            return self._build_error("missing_token")

        token = auth_header.split(" ", 1)[1].strip()
        try:
            payload = verify_session_token(token)
        except ShopifyOAuthError as exc:
            return self._build_error(str(exc))
        except Exception:
            return self._build_error("invalid_token")

        shop = normalise_shop_domain(payload.get("dest", "").replace("https://", ""))
        if not shop:
            return self._build_error("shop_missing")

        request.shop_domain = shop
        request.shopify_session = payload
        logger.info(
            "SESSION_TOKEN_VERIFIED",
            extra={"shop": shop, "request_id": getattr(request, "request_id", "")},
        )

        response = self.get_response(request)
        return response
