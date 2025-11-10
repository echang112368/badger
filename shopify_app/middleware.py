"""Custom middleware for Shopify embedded app integration."""

from __future__ import annotations

from django.conf import settings


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
