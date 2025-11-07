import hashlib
import hmac
import uuid
from typing import Dict

import requests
from django.conf import settings
from django.contrib.auth import login as auth_login
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status

from accounts.forms import CustomLoginForm
from merchants.models import MerchantMeta, MerchantTeamMember
from .discounts import select_discount_percentage
from .forms import ShopifyOAuthSignupForm
from .shopify_client import ShopifyClient


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def create_discount(request, merchant_uuid):
    """Create a discount code based on lottery probabilities."""
    try:
        meta = MerchantMeta.objects.get(uuid=merchant_uuid)
    except MerchantMeta.DoesNotExist:
        return Response({"error": "Invalid merchant"}, status=status.HTTP_400_BAD_REQUEST)

    if not meta.shopify_access_token or not meta.shopify_store_domain:
        return Response(
            {"error": "Missing Shopify credentials"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    percentage = select_discount_percentage()
    if percentage is None:
        return Response({"discount": None, "message": "No discount awarded"})

    client = ShopifyClient(meta.shopify_access_token, meta.shopify_store_domain)

    coupon_code = f"BADGER-{uuid.uuid4().hex[:8].upper()}"
    now = timezone.now()
    price_rule_payload = {
        "price_rule": {
            "title": coupon_code,
            "target_type": "line_item",
            "target_selection": "all",
            "allocation_method": "across",
            "value_type": "percentage",
            "value": f"-{float(percentage):.1f}",
            "customer_selection": "all",
            "starts_at": now.isoformat(),
            "ends_at": (now + timezone.timedelta(days=1)).isoformat(),
            "usage_limit": 1,
        }
    }

    response = client.post("/admin/api/2024-07/price_rules.json", json=price_rule_payload)
    price_rule_id = response.json()["price_rule"]["id"]

    discount_payload = {"discount_code": {"code": coupon_code}}
    client.post(
        f"/admin/api/2024-07/price_rules/{price_rule_id}/discount_codes.json",
        json=discount_payload,
    )

    return Response({"coupon_code": coupon_code, "discount": percentage})


OAUTH_SESSION_KEY = "shopify_oauth_pending"


def _validate_shopify_hmac(params) -> bool:
    provided_hmac = params.get("hmac")
    if not provided_hmac:
        return False
    items = [
        (key, ",".join(params.getlist(key)))
        for key in params.keys()
        if key != "hmac"
    ]
    message = "&".join(f"{key}={value}" for key, value in sorted(items))
    digest = hmac.new(
        settings.SHOPIFY_API_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(digest, provided_hmac)


def _exchange_code_for_token(shop_domain: str, code: str) -> Dict[str, str]:
    url = f"https://{shop_domain}/admin/oauth/access_token"
    payload = {
        "client_id": settings.SHOPIFY_API_KEY,
        "client_secret": settings.SHOPIFY_API_SECRET,
        "code": code,
    }
    response = requests.post(url, json=payload, timeout=10)
    response.raise_for_status()
    return response.json()


def _ensure_merchant_membership(user):
    membership = getattr(user, "merchant_team_membership", None)
    if membership is None:
        membership = MerchantTeamMember.objects.filter(user=user).first()
        if membership is not None:
            setattr(user, "merchant_team_membership", membership)
    if membership and membership.merchant:
        return membership.merchant
    if not user.is_merchant:
        user.is_merchant = True
        user.save(update_fields=["is_merchant"])
    membership, _ = MerchantTeamMember.objects.get_or_create(
        user=user,
        defaults={
            "merchant": user,
            "role": MerchantTeamMember.Role.SUPERUSER,
        },
    )
    setattr(user, "merchant_team_membership", membership)
    return membership.merchant


def _persist_shopify_credentials(user, oauth_data: Dict[str, str], company_name: str = ""):
    merchant_user = _ensure_merchant_membership(user)
    meta, _ = MerchantMeta.objects.get_or_create(user=merchant_user)
    access_token = oauth_data.get("access_token", "")
    store_domain = (oauth_data.get("shop") or "").lower()
    meta.shopify_access_token = access_token
    meta.shopify_store_domain = store_domain
    meta.business_type = MerchantMeta.BusinessType.SHOPIFY
    if company_name:
        meta.company_name = company_name
    if access_token:
        meta.shopify_oauth_authorization_line = f"Authorization: Bearer {access_token}"
    meta.save()
    return meta


@require_http_methods(["GET", "POST"])
def oauth_callback(request):
    if request.method == "GET":
        if not settings.SHOPIFY_API_KEY or not settings.SHOPIFY_API_SECRET:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {
                    "error": "Shopify credentials are not configured.",
                },
                status=500,
            )

        if not _validate_shopify_hmac(request.GET):
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": "Invalid Shopify signature."},
                status=400,
            )

        state = request.GET.get("state")
        expected_state = request.session.get("shopify_oauth_state")
        if expected_state and state != expected_state:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": "The OAuth session has expired. Start the installation again."},
                status=400,
            )

        code = request.GET.get("code")
        shop = request.GET.get("shop")
        if not code or not shop:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": "Missing required Shopify OAuth parameters."},
                status=400,
            )

        try:
            token_payload = _exchange_code_for_token(shop, code)
        except requests.RequestException as exc:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": f"Failed to exchange access token: {exc}"},
                status=502,
            )

        access_token = token_payload.get("access_token")
        if not access_token:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": "Shopify did not return an access token."},
                status=502,
            )

        request.session[OAUTH_SESSION_KEY] = {
            "shop": shop,
            "access_token": access_token,
            "scope": token_payload.get("scope", ""),
        }
        request.session.modified = True
        signup_form = ShopifyOAuthSignupForm()
        login_form = CustomLoginForm(request)
    else:
        oauth_data = request.session.get(OAUTH_SESSION_KEY)
        if not oauth_data:
            return render(
                request,
                "shopify_app/oauth_connect_error.html",
                {"error": "The Shopify OAuth session could not be found. Start the installation again."},
                status=400,
            )

        action = request.POST.get("action")
        if action == "signup":
            signup_form = ShopifyOAuthSignupForm(request.POST)
            login_form = CustomLoginForm(request)
            if signup_form.is_valid():
                user = signup_form.save()
                _persist_shopify_credentials(user, oauth_data, signup_form.company_name)
                auth_login(request, user)
                request.session.pop(OAUTH_SESSION_KEY, None)
                request.session.pop("shopify_oauth_state", None)
                return redirect(f"{reverse('merchant_settings')}?tab=api")
        elif action == "login":
            signup_form = ShopifyOAuthSignupForm()
            login_form = CustomLoginForm(request, data=request.POST)
            if login_form.is_valid():
                user = login_form.get_user()
                auth_login(request, user)
                _persist_shopify_credentials(user, oauth_data)
                request.session.pop(OAUTH_SESSION_KEY, None)
                request.session.pop("shopify_oauth_state", None)
                return redirect(f"{reverse('merchant_settings')}?tab=api")
        else:
            signup_form = ShopifyOAuthSignupForm()
            login_form = CustomLoginForm(request)

    context = {
        "signup_form": signup_form,
        "login_form": login_form,
        "shop_domain": request.session.get(OAUTH_SESSION_KEY, {}).get("shop"),
    }
    return render(request, "shopify_app/oauth_connect.html", context)
