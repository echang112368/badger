from decimal import Decimal, ROUND_HALF_UP
from datetime import timedelta
import json
import json
import logging
import secrets

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count, Q
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from links.models import (
    MerchantCreatorLink,
    STATUS_REQUESTED,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
)
from creators.models import CreatorMeta
from .forms import (
    MerchantSettingsForm,
    ItemGroupForm,
    TeamMemberCreateForm,
    TeamMemberUpdateForm,
)
from accounts.forms import UserNameForm
from accounts.models import CustomUser
from .models import MerchantItem, MerchantMeta, ItemGroup, MerchantTeamMember
from shopify_app import billing as shopify_billing
from shopify_app.shopify_client import ShopifyClient, ShopifyInvalidCredentialsError
from shopify_app.token_management import clear_shopify_token_for_shop, refresh_shopify_token
from shopify_app.oauth import normalise_shop_domain, session_refresh_key, session_token_key
from shopify_app.views import build_shopify_authorize_url
from shopify_app.webhooks import register_orders_create_webhook
from ledger.models import LedgerEntry, MerchantInvoice
from django.http import HttpResponseForbidden, JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import urlparse, urlencode
from django.urls import reverse
from typing import Iterable, Optional
from django.utils.text import slugify

from collect.models import AffiliateClick, ReferralVisit

from .access import resolve_merchant_permissions


logger = logging.getLogger(__name__)


def _normalize_domain(domain: str) -> str:
    if not domain:
        return ""
    parsed = urlparse(domain if "://" in domain else f"//{domain}")
    host = parsed.netloc or parsed.path
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


_SETTINGS_TABS = {"profile", "billing", "notifications", "integrations", "api", "team"}
SHOPIFY_BILLING_STATUS_TTL = timedelta(minutes=5)

SOCIAL_PLATFORM_OPTIONS = [
    "YouTube",
    "TikTok",
    "Instagram",
    "Snapchat",
    "Twitter / X",
    "Facebook",
    "Twitch",
    "Discord",
    "Reddit",
    "Pinterest",
    "LinkedIn",
    "Blog / Website",
    "Newsletter (Substack, Beehiiv, etc.)",
    "Podcast",
    "Other",
]

FOLLOWER_RANGE_OPTIONS = [
    "0–1k",
    "1k–5k",
    "5k–10k",
    "10k–50k",
    "50k–100k",
    "100k–500k",
    "500k–1M",
    "1M–2M",
    "2M–3M",
    "3M–4M",
    "4M–5M",
    "5M–6M",
    "6M–7M",
    "7M–8M",
    "8M–9M",
    "9M–10M",
    "10M+",
]


def _get_merchant_meta(merchant_user: Optional[CustomUser]) -> Optional[MerchantMeta]:
    """Safely return the merchant's ``MerchantMeta`` instance if it exists."""

    if merchant_user is None:
        return None

    try:
        return merchant_user.merchantmeta
    except MerchantMeta.DoesNotExist:
        return None


def _should_refresh_shopify_billing(request, meta: Optional[MerchantMeta]) -> bool:
    if not meta or meta.business_type != MerchantMeta.BusinessType.SHOPIFY:
        return False
    if meta.requires_shopify_oauth():
        return False
    if request.session.pop("shopify_billing_refresh_required", False):
        return True
    verified_at = getattr(meta, "shopify_billing_verified_at", None)
    if not verified_at:
        return True
    return timezone.now() - verified_at > SHOPIFY_BILLING_STATUS_TTL


def _should_show_invoices_tab(merchant_meta: Optional[MerchantMeta]) -> bool:
    """Return ``True`` when the invoices tab should be displayed."""

    if not merchant_meta:
        return True
    return merchant_meta.business_type != MerchantMeta.BusinessType.SHOPIFY


def _get_shopify_client(merchant_meta: Optional[MerchantMeta]):
    if not merchant_meta or not merchant_meta.shopify_access_token or not merchant_meta.shopify_store_domain:
        return None

    return ShopifyClient(
        merchant_meta.shopify_access_token,
        merchant_meta.shopify_store_domain,
        refresh_handler=lambda: refresh_shopify_token(merchant_meta),
        token_type="offline",
    )


def _fetch_shopify_products(client: Optional[ShopifyClient], product_ids: Iterable[str]):
    if not client:
        return []
    try:
        return client.get_products_by_ids(product_ids)
    except Exception:
        logger.exception("Failed to fetch Shopify product details")
        return []


def _build_product_link(product: dict, shopify_domain: str) -> str:
    if not product:
        return f"https://{shopify_domain}" if shopify_domain else "https://shopify.com"

    online_store_url = product.get("onlineStoreUrl")
    if online_store_url:
        return online_store_url

    handle = product.get("handle")
    if handle and shopify_domain:
        return f"https://{shopify_domain}/products/{handle}"

    return f"https://{shopify_domain}" if shopify_domain else "https://shopify.com"


def _attempt_shopify_webhook_registration(
    request,
    merchant_meta: Optional[MerchantMeta],
    shop_domain: str,
) -> None:
    if not merchant_meta or not merchant_meta.shopify_access_token or not shop_domain:
        return
    webhook_url = request.build_absolute_uri(
        reverse("shopify_orders_create_webhook")
    )
    try:
        register_orders_create_webhook(
            shop_domain,
            merchant_meta.shopify_access_token,
            webhook_url=webhook_url,
        )
    except Exception:
        logger.exception(
            "Failed to register Shopify orders/create webhook for %s.",
            shop_domain,
        )


def _generate_team_email(merchant: CustomUser, username: str) -> str:
    merchant_identifier = slugify(merchant.username) or slugify(getattr(merchant, "email", ""))
    if not merchant_identifier:
        company_name = ""
        try:
            company_name = merchant.merchantmeta.company_name
        except MerchantMeta.DoesNotExist:
            company_name = ""
        merchant_identifier = slugify(company_name) or "merchant"
    domain = f"{merchant_identifier}.team.badger"
    base_email = f"{username}@{domain}"
    email = base_email
    counter = 1
    while CustomUser.objects.filter(email=email).exists():
        counter += 1
        email = f"{username}{counter}@{domain}"
    return email


def _build_shopify_reauth_payload(
    request, shop_domain: str, message: str = ""
) -> dict:
    normalised = normalise_shop_domain(shop_domain)
    clear_shopify_token_for_shop(normalised)
    authorize_url = build_shopify_authorize_url(request, normalised)
    logger.warning(
        "Shopify credentials for %s are invalid. Prompting merchant to reinstall.",
        normalised,
    )
    return {
        "error": message
        or "Shopify rejected the request because the stored credentials are invalid."
        " Please reinstall the Shopify app to continue.",
        "authorize_url": authorize_url,
        "shop_domain": normalised,
    }


def _create_team_member_account(merchant: CustomUser, form: TeamMemberCreateForm):
    username = form.generate_username(merchant)
    email = form.cleaned_data["email"] or _generate_team_email(merchant, username)
    password = secrets.token_urlsafe(12)
    user = CustomUser.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name=form.cleaned_data["first_name"],
        last_name=form.cleaned_data["last_name"],
        is_active=True,
        is_merchant=False,
    )
    membership = MerchantTeamMember.objects.create(
        merchant=merchant,
        user=user,
        role=form.cleaned_data["role"],
    )
    return user, password, membership


def _resolve_settings_tab(tab: Optional[str]) -> str:
    """Return a valid settings tab slug."""

    if not tab:
        return "profile"
    tab = tab.strip().lower()
    return tab if tab in _SETTINGS_TABS else "profile"


def _enforce_tab_permissions(tab: str, permissions) -> str:
    if tab == "api" and not permissions.can_manage_api:
        return "profile"
    if tab == "team" and not permissions.can_view_team:
        return "profile"
    return tab


@csrf_exempt
@require_GET
def store_id_lookup(request):
    domain = _normalize_domain(request.GET.get("domain", ""))
    store_id = None
    if domain:
        for meta in MerchantMeta.objects.all():
            if _normalize_domain(meta.shopify_store_domain) == domain:
                store_id = str(meta.uuid)
                break

    response = JsonResponse({"storeID": store_id})
    response["Access-Control-Allow-Origin"] = "*"
    return response


@login_required
def merchant_dashboard(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return redirect('login')

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)

    if merchant_meta and merchant_meta.requires_shopify_oauth():
        shop_domain = normalise_shop_domain(merchant_meta.shopify_store_domain)
        if shop_domain:
            authorize_url = (
                f"{reverse('shopify_oauth_authorize')}?"
                f"{urlencode({'shop': shop_domain})}"
            )
            return redirect(authorize_url)

    balance = LedgerEntry.merchant_balance(merchant_user)
    entries = (
        LedgerEntry.objects.filter(merchant=merchant_user)
        .exclude(entry_type=LedgerEntry.EntryType.COMMISSION)
        .order_by('-timestamp')
    )
    affiliate_total_raw = (
        LedgerEntry.objects.filter(
            merchant=merchant_user,
            entry_type=LedgerEntry.EntryType.AFFILIATE_PAYOUT,
            paid=False,
        ).aggregate(total=Sum("amount"))
    ).get("total") or Decimal("0")
    affiliate_total = (
        -affiliate_total_raw if affiliate_total_raw < 0 else affiliate_total_raw
    )
    affiliate_total = affiliate_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return render(request, 'merchants/dashboard.html', {
        'merchant': merchant_user,
        'balance': balance,
        'ledger_entries': entries,
        'permissions': permissions,
        'affiliate_total': affiliate_total,
        'show_invoices_tab': _should_show_invoices_tab(merchant_meta),
    })


@login_required
def merchant_marketplace(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return redirect('login')

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)

    if not merchant_meta:
        return redirect('merchant_dashboard')

    if request.method == "POST":
        if not permissions.can_modify_content:
            return redirect("merchant_marketplace")
        merchant_meta.marketplace_enabled = bool(
            request.POST.get("marketplace_enabled")
        )
        merchant_meta.save(update_fields=["marketplace_enabled"])
        return redirect("merchant_marketplace")

    query = (request.GET.get("q") or "").strip()
    platform = (request.GET.get("platform") or "").strip()
    follower_range = (request.GET.get("follower_range") or "").strip()
    country = (request.GET.get("country") or "").strip()
    language = (request.GET.get("language") or "").strip()
    skill = (request.GET.get("skill") or "").strip()
    creator_cards = []
    if merchant_meta.marketplace_enabled:
        creator_qs = (
            CreatorMeta.objects.select_related("user")
            .filter(marketplace_enabled=True)
            .order_by("user__first_name", "user__last_name", "user__username")
        )
        if query:
            creator_qs = creator_qs.filter(
                Q(user__first_name__icontains=query)
                | Q(user__last_name__icontains=query)
                | Q(user__username__icontains=query)
                | Q(user__email__icontains=query)
                | Q(country__icontains=query)
                | Q(content_languages__icontains=query)
            )
        if platform:
            creator_qs = creator_qs.filter(social_media_platform__icontains=platform)
        if follower_range:
            creator_qs = creator_qs.filter(follower_range__iexact=follower_range)
        if country:
            creator_qs = creator_qs.filter(country__icontains=country)
        if language:
            creator_qs = creator_qs.filter(content_languages__icontains=language)
        if skill:
            creator_qs = creator_qs.filter(content_skills__contains=[skill])

        creator_cards = list(creator_qs)

    return render(
        request,
        "merchants/marketplace.html",
        {
            "merchant": merchant_user,
            "merchant_meta": merchant_meta,
            "permissions": permissions,
            "creator_cards": creator_cards,
            "query": query,
            "platform": platform,
            "follower_range": follower_range,
            "country": country,
            "language": language,
            "skill": skill,
            "social_platform_options": SOCIAL_PLATFORM_OPTIONS,
            "follower_range_options": FOLLOWER_RANGE_OPTIONS,
            "show_invoices_tab": _should_show_invoices_tab(merchant_meta),
        },
    )


@login_required
def merchant_invoices(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return redirect('login')

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)

    show_invoices_tab = _should_show_invoices_tab(merchant_meta)

    if not show_invoices_tab:
        return redirect('merchant_dashboard')

    invoices_qs = (
        MerchantInvoice.objects.filter(merchant=merchant_user)
        .order_by('-created_at')
    )

    from ledger.invoices import update_invoice_status

    invoices = []
    for invoice in invoices_qs:
        update_invoice_status(invoice)
        invoice.refresh_from_db()
        invoices.append(invoice)

    open_invoices = [invoice for invoice in invoices if invoice.status != "PAID"]
    paid_invoices = [invoice for invoice in invoices if invoice.status == "PAID"]
    shopify_invoices = [
        invoice
        for invoice in invoices
        if invoice.provider == MerchantInvoice.Provider.SHOPIFY
    ]
    is_shopify_merchant = bool(
        merchant_meta
        and merchant_meta.business_type == MerchantMeta.BusinessType.SHOPIFY
    )
    billing_status = (merchant_meta.shopify_billing_status or "") if merchant_meta else ""
    shopify_pending_confirmation = bool(
        is_shopify_merchant
        and (
            not merchant_meta.shopify_recurring_charge_id
            or billing_status.lower() != "active"
        )
    )

    return render(
        request,
        'merchants/invoices.html',
        {
            'merchant': merchant_user,
            'permissions': permissions,
            'open_invoices': open_invoices,
            'paid_invoices': paid_invoices,
            'shopify_invoices': shopify_invoices,
            'all_invoices': invoices,
            'merchant_meta': merchant_meta,
            'is_shopify_merchant': is_shopify_merchant,
            'shopify_pending_confirmation': shopify_pending_confirmation,
            'show_invoices_tab': show_invoices_tab,
        },
    )


@login_required
@require_GET
def search_shopify_products(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return JsonResponse({"error": "Forbidden"}, status=403)

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)
    client = _get_shopify_client(merchant_meta)

    authorize_url = build_shopify_authorize_url(
        request, merchant_meta.shopify_store_domain if merchant_meta else ""
    )

    if not client:
        return JsonResponse(
            {
                "products": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "error": "Shopify store not connected.",
                "authorize_url": authorize_url,
            },
            status=400,
        )

    query = (request.GET.get("q") or "").strip()
    cursor = request.GET.get("cursor") or None
    try:
        limit = int(request.GET.get("limit", 20))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 50))

    if not query:
        return JsonResponse(
            {"products": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}
        )

    try:
        results = client.search_products(query=query, cursor=cursor, limit=limit)
    except ShopifyInvalidCredentialsError:
        payload = _build_shopify_reauth_payload(
            request,
            merchant_meta.shopify_store_domain if merchant_meta else "",
            message="Shopify disconnected. Please reconnect to search products.",
        )
        return JsonResponse(payload, status=401)
    except Exception:
        logger.exception("Failed to search Shopify products")
        return JsonResponse(
            {"error": "Unable to search products at this time."}, status=502
        )

    def _serialize_product(product: dict) -> dict:
        image = (product.get("featuredImage") or {}).get("src")
        if not image:
            images = product.get("images") or []
            if images:
                image = images[0].get("src")
        return {
            "id": product.get("id"),
            "title": product.get("title"),
            "productType": product.get("productType"),
            "handle": product.get("handle"),
            "image": image,
            "variants": [
                variant.get("title")
                for variant in product.get("variants", [])
                if variant.get("title")
            ],
        }

    return JsonResponse(
        {
            "products": [_serialize_product(product) for product in results.get("products", [])],
            "pageInfo": results.get("pageInfo", {}),
        }
    )


@login_required
@require_GET
def list_shopify_products(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return JsonResponse({"error": "Forbidden"}, status=403)

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)
    client = _get_shopify_client(merchant_meta)

    authorize_url = build_shopify_authorize_url(
        request, merchant_meta.shopify_store_domain if merchant_meta else ""
    )

    if not client:
        return JsonResponse(
            {
                "products": [],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
                "error": "Shopify store not connected.",
                "authorize_url": authorize_url,
            },
            status=400,
        )

    cursor = request.GET.get("cursor") or None
    try:
        limit = int(request.GET.get("limit", 20))
    except ValueError:
        limit = 20
    limit = max(1, min(limit, 50))

    try:
        results = client.list_products(cursor=cursor, limit=limit)
    except ShopifyInvalidCredentialsError:
        payload = _build_shopify_reauth_payload(
            request,
            merchant_meta.shopify_store_domain if merchant_meta else "",
            message="Shopify disconnected. Please reconnect to load products.",
        )
        return JsonResponse(payload, status=401)
    except Exception:
        logger.exception("Failed to list Shopify products")
        return JsonResponse(
            {"error": "Unable to load products at this time."}, status=502
        )

    def _serialize_product(product: dict) -> dict:
        image = (product.get("featuredImage") or {}).get("src")
        if not image:
            images = product.get("images") or []
            if images:
                image = images[0].get("src")
        return {
            "id": product.get("id"),
            "title": product.get("title"),
            "productType": product.get("productType"),
            "handle": product.get("handle"),
            "image": image,
            "variants": [
                variant.get("title")
                for variant in product.get("variants", [])
                if variant.get("title")
            ],
        }

    return JsonResponse(
        {
            "products": [_serialize_product(product) for product in results.get("products", [])],
            "pageInfo": results.get("pageInfo", {}),
        }
    )

@login_required
def merchant_items(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return redirect('login')

    merchant_user = permissions.merchant

    shopify_domain = ""
    merchant_meta = _get_merchant_meta(merchant_user)
    shopify_client = _get_shopify_client(merchant_meta)
    shopify_authorize_url = build_shopify_authorize_url(
        request, merchant_meta.shopify_store_domain if merchant_meta else ""
    )
    if merchant_meta and merchant_meta.shopify_store_domain:
        shopify_domain = merchant_meta.shopify_store_domain

    if request.method == "POST":
        if not permissions.can_modify_content:
            return redirect('login')
        if request.POST.get("form_type") == "group":
            group_id = request.POST.get("group_id")
            group = (
                ItemGroup.objects.filter(id=group_id, merchant=merchant_user).first()
                if group_id
                else None
            )
            group_form = ItemGroupForm(
                request.POST, instance=group, merchant=merchant_user, prefix="group"
            )
            selected_items = request.POST.getlist("shopify_items")
            if group_form.is_valid():
                group = group_form.save(commit=False)
                group.merchant = merchant_user
                existing_items = {
                    item.shopify_product_id: item
                    for item in MerchantItem.objects.filter(
                        merchant=merchant_user, shopify_product_id__in=selected_items
                    ).prefetch_related("groups")
                }
                conflicts = []
                non_conflicting_items = []
                for pid in selected_items:
                    item = existing_items.get(pid)
                    if item:
                        conflicting_groups = item.groups
                        if group:
                            conflicting_groups = conflicting_groups.exclude(pk=group.pk)
                        if conflicting_groups.exists():
                            conflicts.append(item.title or f"Shopify product {pid}")
                            continue
                    non_conflicting_items.append(pid)

                if conflicts:
                    post_data = request.POST.dict()
                    post_data.pop("csrfmiddlewaretoken", None)
                    request.session["group_form_post"] = post_data
                    request.session["group_form_selected"] = selected_items
                    request.session["group_form_conflicts"] = conflicts
                    return redirect("merchant_items")

                items_to_add = []
                product_details = {
                    str(product["id"]): product
                    for product in _fetch_shopify_products(shopify_client, non_conflicting_items)
                }
                for pid in non_conflicting_items:
                    product = product_details.get(pid, {})
                    featured_image = ((product or {}).get("featuredImage") or {}).get("src")
                    if not featured_image:
                        images = (product or {}).get("images") or []
                        if images:
                            featured_image = images[0].get("src")
                    variants = (product or {}).get("variants") or []
                    variant_price = variants[0].get("price") if variants else None
                    item = existing_items.get(pid)
                    if not item:
                        item = MerchantItem.objects.create(
                            merchant=merchant_user,
                            shopify_product_id=str(pid),
                            title=product.get("title") or f"Shopify product {pid}",
                            link=_build_product_link(product, shopify_domain),
                            image_url=featured_image,
                            price=variant_price,
                        )
                    else:
                        if product:
                            updated = False
                            if product.get("title") and item.title != product["title"]:
                                item.title = product["title"]
                                updated = True
                            product_link = _build_product_link(product, shopify_domain)
                            if product_link and item.link != product_link:
                                item.link = product_link
                                updated = True
                            if featured_image and item.image_url != featured_image:
                                item.image_url = featured_image
                                updated = True
                            if variant_price is not None and item.price != variant_price:
                                item.price = variant_price
                                updated = True
                            if updated:
                                item.save(
                                    update_fields=[
                                        "title",
                                        "link",
                                        "image_url",
                                        "price",
                                    ]
                                )

                    items_to_add.append(item)
                group.save()
                group.items.set(items_to_add)
                return redirect("merchant_items")
            else:
                post_data = request.POST.dict()
                post_data.pop("csrfmiddlewaretoken", None)
                request.session["group_form_post"] = post_data
                request.session["group_form_selected"] = selected_items
                return redirect("merchant_items")
        elif request.POST.get("form_type") == "delete_group":
            group_id = request.POST.get("group_id")
            group = ItemGroup.objects.filter(id=group_id, merchant=merchant_user).first()
            if group:
                group.delete()
            return redirect("merchant_items")

    post_data = request.session.pop("group_form_post", None)
    selected_items = request.session.pop("group_form_selected", [])
    conflicts = request.session.pop("group_form_conflicts", [])
    if post_data:
        qdict = QueryDict("", mutable=True)
        for k, v in post_data.items():
            qdict[k] = v
        for item in selected_items:
            qdict.appendlist("shopify_items", item)
        group_id = qdict.get("group_id")
        group = (
            ItemGroup.objects.filter(id=group_id, merchant=merchant_user).first()
            if group_id
            else None
        )
        group_form = ItemGroupForm(qdict, instance=group, merchant=merchant_user, prefix="group")
        group_form.is_valid()
        if conflicts:
            group_form.add_error(
                None,
                "The following items are already in another group: " + ", ".join(conflicts),
            )
    else:
        group_form = ItemGroupForm(merchant=merchant_user, prefix="group")
        selected_items = []

    groups = ItemGroup.objects.filter(merchant=merchant_user).prefetch_related("items")

    selected_products_data = []
    if selected_items:
        products_from_shopify = _fetch_shopify_products(shopify_client, selected_items)
        products_by_id = {str(prod.get("id")): prod for prod in products_from_shopify}
        existing_items = {
            item.shopify_product_id: item
            for item in MerchantItem.objects.filter(
                merchant=merchant_user, shopify_product_id__in=selected_items
            )
        }
        for pid in selected_items:
            product = products_by_id.get(pid)
            fallback_item = existing_items.get(pid)
            selected_products_data.append(
                {
                    "id": str(pid),
                    "title": (product or {}).get("title")
                    or (fallback_item.title if fallback_item else ""),
                    "image": ((product or {}).get("featuredImage") or {}).get("src"),
                    "variants": [v.get("title") for v in (product or {}).get("variants", []) if v.get("title")],
                }
            )

    return render(
        request,
        "merchants/items.html",
        {
            "merchant": merchant_user,
            "groups": groups,
            "group_form": group_form,
            "shopify_domain": shopify_domain,
            "selected_shopify_items": selected_items,
            "group_modal_open": bool(group_form.errors),
            "permissions": permissions,
            "show_invoices_tab": _should_show_invoices_tab(merchant_meta),
            "selected_products_data": json.dumps(selected_products_data),
            "shopify_connected": bool(shopify_client),
            "shopify_authorize_url": shopify_authorize_url,
        },
    )

@login_required
def delete_item(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_modify_content:
        return HttpResponseForbidden()

    merchant_user = permissions.merchant
    if request.method == 'POST':
        ids = request.POST.getlist('selected_items')
        for item_id in ids:
            item = MerchantItem.objects.filter(id=item_id, merchant=merchant_user).first()
            if item:
                item.delete()
        return redirect('merchant_dashboard')
    return HttpResponseForbidden()

@login_required
def delete_creators(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_modify_content:
        return HttpResponseForbidden()

    merchant_user = permissions.merchant
    if request.method == "POST":
        creator_ids = request.POST.getlist("selected_creators")
        qs = MerchantCreatorLink.objects.filter(
            merchant=merchant_user, creator__id__in=creator_ids
        )
        default_creator = CustomUser.get_default_badger_creator()
        if default_creator:
            qs = qs.exclude(creator=default_creator)
        qs.delete()

    return redirect("merchant_creators")


@login_required
def update_creator_status(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_modify_content:
        return HttpResponseForbidden()

    merchant_user = permissions.merchant
    if request.method == "POST":
        creator_ids = request.POST.getlist("selected_creators")
        action = request.POST.get("action")
        if creator_ids and action in ["activate", "deactivate"]:
            qs = MerchantCreatorLink.objects.filter(
                merchant=merchant_user, creator__id__in=creator_ids
            )
            default_creator = CustomUser.get_default_badger_creator()
            if default_creator:
                qs = qs.exclude(creator=default_creator)
            qs.update(
                status=STATUS_ACTIVE if action == "activate" else STATUS_INACTIVE
            )
    return redirect("merchant_creators")


@login_required
def request_creator(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_modify_content:
        return HttpResponseForbidden()

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)
    if request.method == "POST":
        uuid = request.POST.get("creator_uuid", "").strip()
        if uuid:
            try:
                creator_meta = CreatorMeta.objects.get(uuid=uuid)
                existing_link = MerchantCreatorLink.objects.filter(
                    merchant=merchant_user,
                    creator=creator_meta.user,
                ).first()
                creator_limit = merchant_meta.creator_limit if merchant_meta else None
                if creator_limit is not None and existing_link is None:
                    current_count = MerchantCreatorLink.objects.filter(
                        merchant=merchant_user
                    ).count()
                    if current_count >= creator_limit:
                        return redirect("merchant_creators")
                link, created = MerchantCreatorLink.objects.get_or_create(
                    merchant=merchant_user,
                    creator=creator_meta.user,
                    defaults={"status": STATUS_REQUESTED},
                )
                if not created and link.status != STATUS_ACTIVE:
                    link.status = STATUS_REQUESTED
                    link.save()
            except CreatorMeta.DoesNotExist:
                pass
    return redirect("merchant_creators")


@login_required
def merchant_creators(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return redirect('login')

    merchant_user = permissions.merchant
    merchant_meta = _get_merchant_meta(merchant_user)

    active_links = (
        MerchantCreatorLink.objects.filter(
            merchant=merchant_user, status=STATUS_ACTIVE
        )
        .select_related("creator__creatormeta")
        .order_by("creator__username")
    )
    inactive_links = (
        MerchantCreatorLink.objects.filter(
            merchant=merchant_user, status=STATUS_INACTIVE
        )
        .select_related("creator__creatormeta")
        .order_by("creator__username")
    )
    pending_links = (
        MerchantCreatorLink.objects.filter(
            merchant=merchant_user, status=STATUS_REQUESTED
        )
        .select_related("creator__creatormeta")
        .order_by("creator__username")
    )

    def quantize_amount(value):
        if value is None:
            value = Decimal("0")
        elif not isinstance(value, Decimal):
            value = Decimal(value)
        return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    start_of_month = timezone.now().replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )

    commission_entries = LedgerEntry.objects.filter(
        merchant=merchant_user,
        entry_type=LedgerEntry.EntryType.COMMISSION,
        creator__isnull=False,
    )

    totals_by_creator = {
        row["creator"]: row["total"]
        for row in commission_entries.values("creator").annotate(total=Sum("amount"))
    }
    monthly_totals_by_creator = {
        row["creator"]: row["total"]
        for row in commission_entries.filter(timestamp__gte=start_of_month)
        .values("creator")
        .annotate(total=Sum("amount"))
    }
    conversion_counts = {
        row["creator"]: row["count"]
        for row in commission_entries.filter(amount__gt=0)
        .values("creator")
        .annotate(count=Count("id"))
    }

    affiliate_clicks_by_uuid = {}
    if merchant_meta:
        for row in (
            AffiliateClick.objects.filter(storeID=merchant_meta.uuid)
            .values("uuid")
            .annotate(count=Count("id"))
        ):
            affiliate_clicks_by_uuid[str(row["uuid"])] = row["count"]

    visits_by_creator = {}
    visits_by_uuid = {}
    for row in (
        ReferralVisit.objects.filter(merchant=merchant_user)
        .values("creator_id", "creator_uuid")
        .annotate(count=Count("id"))
    ):
        creator_id = row["creator_id"]
        creator_uuid = row["creator_uuid"]
        if creator_id:
            visits_by_creator[creator_id] = row["count"]
        if creator_uuid:
            visits_by_uuid[str(creator_uuid)] = row["count"]

    def build_creator_entry(link):
        creator = link.creator
        creator_meta = getattr(creator, "creatormeta", None)
        creator_id = creator.id

        total_earnings = quantize_amount(totals_by_creator.get(creator_id))
        monthly_earnings = quantize_amount(
            monthly_totals_by_creator.get(creator_id)
        )
        conversions = conversion_counts.get(creator_id, 0)

        visits = None
        if creator_meta:
            visits = affiliate_clicks_by_uuid.get(str(creator_meta.uuid))
        if visits is None:
            visits = visits_by_creator.get(creator_id)
        if visits is None and creator_meta:
            visits = visits_by_uuid.get(str(creator_meta.uuid))
        visits = visits or 0

        if visits:
            avg = (total_earnings / Decimal(visits)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            conversion_rate = (
                (Decimal(conversions) / Decimal(visits)) * Decimal("100")
            ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        else:
            avg = Decimal("0.00")
            conversion_rate = Decimal("0.00")

        return {
            "link_id": link.id,
            "creator_id": creator.id,
            "username": creator.username,
            "email": creator.email,
            "short_pitch": creator_meta.short_pitch if creator_meta else "",
            "total_earnings": total_earnings,
            "monthly_earnings": monthly_earnings,
            "visits": visits,
            "conversions": conversions,
            "avg_earnings_per_visit": avg,
            "conversion_rate": conversion_rate,
        }

    def get_short_pitch(creator):
        creator_meta = getattr(creator, "creatormeta", None)
        return creator_meta.short_pitch if creator_meta else ""

    active_creators = [build_creator_entry(link) for link in active_links]
    inactive_creators = [build_creator_entry(link) for link in inactive_links]
    pending_creators = [
        {
            "link_id": link.id,
            "creator_id": link.creator.id,
            "username": link.creator.username,
            "email": link.creator.email,
            "short_pitch": get_short_pitch(link.creator),
        }
        for link in pending_links
    ]

    return render(
        request,
        'merchants/creators.html',
        {
            'merchant': merchant_user,
            'active_creators': active_creators,
            'inactive_creators': inactive_creators,
            'pending_creators': pending_creators,
            'permissions': permissions,
            'show_invoices_tab': _should_show_invoices_tab(merchant_meta),
        },
    )


@login_required
def merchant_settings(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_settings:
        return redirect('login')

    merchant_user = permissions.merchant
    merchant_meta, _ = MerchantMeta.objects.get_or_create(user=merchant_user)

    team_members = list(
        MerchantTeamMember.objects.filter(merchant=merchant_user)
        .select_related("user")
        .order_by("-created_at")
    )
    team_members_payload = [
        {
            "id": member.id,
            "first_name": member.user.first_name,
            "last_name": member.user.last_name,
            "email": member.user.email,
            "full_name": member.user.get_full_name() or member.user.username,
            "role": member.role,
            "role_label": member.get_role_display(),
            "status": "active" if member.user.is_active else "inactive",
            "is_superuser": member.role == MerchantTeamMember.Role.SUPERUSER,
            "delete_url": reverse("delete_team_member", args=[member.id]),
            "update_url": reverse("update_team_member", args=[member.id]),
        }
        for member in team_members
    ]
    stored_credentials = request.session.pop("team_credentials", None)

    form = MerchantSettingsForm(instance=merchant_meta)
    user_form = UserNameForm(instance=merchant_user)
    team_form = TeamMemberCreateForm(prefix="team")

    active_tab = _resolve_settings_tab(request.GET.get("tab"))
    active_tab = _enforce_tab_permissions(active_tab, permissions)

    if request.method == "POST":
        requested_tab = _resolve_settings_tab(request.POST.get("active_tab"))
        if requested_tab == "team":
            if not permissions.can_invite_team:
                return HttpResponseForbidden()
            team_form = TeamMemberCreateForm(request.POST, prefix="team")
            if team_form.is_valid():
                new_user, password, membership = _create_team_member_account(
                    merchant_user, team_form
                )
                request.session["team_credentials"] = {
                    "name": new_user.get_full_name() or new_user.username,
                    "email": new_user.email,
                    "password": password,
                    "username": new_user.username,
                    "role": membership.get_role_display(),
                }
                return redirect(f"{reverse('merchant_settings')}?tab=team")
            active_tab = "team"
        else:
            if not permissions.can_edit_settings:
                return HttpResponseForbidden()
            post_data = request.POST.copy()
            if not permissions.can_manage_api:
                post_data["shopify_store_domain"] = merchant_meta.shopify_store_domain
            form = MerchantSettingsForm(post_data, instance=merchant_meta)
            user_form = UserNameForm(post_data, instance=merchant_user)
            form_valid = form.is_valid()
            user_form_valid = user_form.is_valid()
            updated_meta = merchant_meta

            if form_valid:
                updated_meta = form.save()
            if user_form_valid:
                user_form.save()

            if form_valid and user_form_valid:
                redirect_tab = _enforce_tab_permissions(requested_tab, permissions)
                redirect_url = reverse("merchant_settings")
                if redirect_tab != "profile":
                    redirect_url = f"{redirect_url}?tab={redirect_tab}"
                if updated_meta.requires_shopify_oauth():
                    shop_domain = normalise_shop_domain(updated_meta.shopify_store_domain)
                    if shop_domain:
                        authorize_url = (
                            f"{reverse('shopify_oauth_authorize')}?"
                            f"{urlencode({'shop': shop_domain})}"
                        )
                        return redirect(authorize_url)
                return redirect(redirect_url)
            active_tab = _enforce_tab_permissions(requested_tab, permissions)

    if not permissions.can_edit_settings:
        for field in form.fields.values():
            field.disabled = True
        for field in user_form.fields.values():
            field.disabled = True

    if not permissions.can_manage_api:
        form.fields["shopify_store_domain"].disabled = True

    shopify_plan_price = getattr(merchant_meta, "monthly_fee", None)
    if not shopify_plan_price or Decimal(shopify_plan_price) <= 0:
        shopify_plan_price = Decimal("30.00")
    if _should_refresh_shopify_billing(request, merchant_meta):
        try:
            shopify_billing.refresh_active_subscriptions(
                merchant_meta,
                expected_plan_name=shopify_billing.expected_shopify_plan_name(merchant_meta),
            )
        except shopify_billing.ShopifyReauthorizationRequired:
            logger.warning(
                "Shopify billing refresh requires reauthorization for %s.",
                merchant_meta.shopify_store_domain,
            )
        except shopify_billing.ShopifyBillingError:
            logger.exception("Failed to refresh Shopify billing status for settings view.")

    shopify_plan_active = (
        merchant_meta.shopify_billing_status == "ACTIVE"
        and merchant_meta.shopify_billing_plan == merchant_meta.billing_plan
    )
    shop_domain = ""
    shopify_cancel_url = ""
    if merchant_meta.shopify_store_domain:
        shop_domain = normalise_shop_domain(merchant_meta.shopify_store_domain)
        if shop_domain:
            shopify_cancel_url = f"https://{shop_domain}/admin/settings/billing"
    if shopify_plan_active and shop_domain:
        _attempt_shopify_webhook_registration(request, merchant_meta, shop_domain)

    return render(request, 'merchants/settings.html', {
        'merchant': merchant_user,
        'merchant_meta': merchant_meta,
        'settings_form': form,
        'user_form': user_form,
        'team_form': team_form,
        'team_members': team_members,
        'new_team_credentials': stored_credentials,
        'active_tab': active_tab,
        'permissions': permissions,
        'team_roles': MerchantTeamMember.Role,
        'team_members_payload': team_members_payload,
        'start_shopify_billing_url': reverse('merchant_start_shopify_billing'),
        'shopify_billing_status_url': reverse('merchant_refresh_shopify_billing_status'),
        'shopify_plan_price': shopify_plan_price,
        'shopify_plan_active': shopify_plan_active,
        'shopify_billing_cancel_url': shopify_cancel_url,
        'show_invoices_tab': _should_show_invoices_tab(merchant_meta),
    })


@login_required
@require_POST
def start_shopify_billing(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_edit_settings:
        return JsonResponse({"error": "You do not have permission to update billing."}, status=403)

    try:
        merchant_meta = request.user.merchantmeta
    except MerchantMeta.DoesNotExist:
        return JsonResponse({"error": "Merchant profile not found."}, status=404)

    if merchant_meta.business_type != MerchantMeta.BusinessType.SHOPIFY:
        return JsonResponse({"error": "Shopify billing is not enabled for this merchant."}, status=400)

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}

    selected_plan = payload.get("billing_plan") or merchant_meta.billing_plan
    valid_plans = {choice for choice, _ in MerchantMeta.BillingPlan.choices}
    if selected_plan not in valid_plans:
        return JsonResponse({"error": "Invalid billing plan selected."}, status=400)

    update_fields = []
    if selected_plan != merchant_meta.billing_plan:
        merchant_meta.billing_plan = selected_plan
        merchant_meta.monthly_fee = merchant_meta.plan_price
        update_fields.extend(["billing_plan", "monthly_fee"])

    if selected_plan == MerchantMeta.BillingPlan.BADGER_CREATOR:
        usage_cap = getattr(settings, "SHOPIFY_USAGE_CAPPED_AMOUNT", None)
        usage_terms = getattr(settings, "SHOPIFY_USAGE_TERMS", "")
        if merchant_meta.shopify_usage_capped_amount != usage_cap:
            merchant_meta.shopify_usage_capped_amount = usage_cap
            update_fields.append("shopify_usage_capped_amount")
        if merchant_meta.shopify_usage_terms != usage_terms:
            merchant_meta.shopify_usage_terms = usage_terms
            update_fields.append("shopify_usage_terms")
    else:
        if merchant_meta.shopify_usage_capped_amount is not None:
            merchant_meta.shopify_usage_capped_amount = None
            update_fields.append("shopify_usage_capped_amount")
        if merchant_meta.shopify_usage_terms:
            merchant_meta.shopify_usage_terms = ""
            update_fields.append("shopify_usage_terms")

    if update_fields:
        merchant_meta.save(update_fields=update_fields)

    shop_domain = normalise_shop_domain(merchant_meta.shopify_store_domain or "")
    if not shop_domain:
        return JsonResponse({"error": "Shopify store domain is required."}, status=400)

    return_url = request.build_absolute_uri(
        f"{reverse('shopify_billing_return')}?{urlencode({'shop': shop_domain})}"
    )

    try:
        result = shopify_billing.create_or_update_recurring_charge(
            merchant_meta,
            return_url=return_url,
        )
    except shopify_billing.ShopifyReauthorizationRequired:
        authorize_url = build_shopify_authorize_url(
            request, merchant_meta.shopify_store_domain or ""
        )
        return JsonResponse(
            {"error": "Please re-authorize the Shopify app.", "authorize_url": authorize_url},
            status=401,
        )
    except shopify_billing.ShopifyBillingError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    plan_active = (
        merchant_meta.shopify_billing_status == "ACTIVE"
        and merchant_meta.shopify_billing_plan == merchant_meta.billing_plan
    )
    _attempt_shopify_webhook_registration(request, merchant_meta, shop_domain)
    return JsonResponse(
        {
            **result,
            "plan_active": plan_active,
            "plan": merchant_meta.shopify_billing_plan or "",
        }
    )

@login_required
@require_GET
def refresh_shopify_billing_status(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_edit_settings:
        return JsonResponse(
            {"error": "You do not have permission to update billing."}, status=403
        )

    try:
        merchant_meta = request.user.merchantmeta
    except MerchantMeta.DoesNotExist:
        return JsonResponse({"error": "Merchant profile not found."}, status=404)

    if merchant_meta.business_type != MerchantMeta.BusinessType.SHOPIFY:
        return JsonResponse({"error": "Shopify billing is not enabled for this merchant."}, status=400)

    try:
        result = shopify_billing.refresh_active_subscriptions(
            merchant_meta,
            expected_plan_name=shopify_billing.expected_shopify_plan_name(merchant_meta),
        )
    except shopify_billing.ShopifyReauthorizationRequired as exc:
        authorize_url = build_shopify_authorize_url(
            request, merchant_meta.shopify_store_domain or ""
        )
        return JsonResponse(
            {"error": str(exc), "authorize_url": authorize_url},
            status=401,
        )
    except shopify_billing.ShopifyBillingError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    plan_active = (
        merchant_meta.shopify_billing_status == "ACTIVE"
        and merchant_meta.shopify_billing_plan == merchant_meta.billing_plan
    )
    shop_domain = normalise_shop_domain(merchant_meta.shopify_store_domain or "")
    if plan_active and shop_domain:
        _attempt_shopify_webhook_registration(request, merchant_meta, shop_domain)
    return JsonResponse(
        {
            "status": merchant_meta.shopify_billing_status or "",
            "charge_id": merchant_meta.shopify_recurring_charge_id or "",
            "plan": merchant_meta.shopify_billing_plan or "",
            "plan_active": plan_active,
            "verified_at": (
                merchant_meta.shopify_billing_verified_at.isoformat()
                if merchant_meta.shopify_billing_verified_at
                else ""
            ),
            "raw": {
                "terms": merchant_meta.shopify_usage_terms or "",
                "capped_amount": str(merchant_meta.shopify_usage_capped_amount)
                if merchant_meta.shopify_usage_capped_amount
                else "",
            },
        }
    )

@login_required
@require_POST
def update_team_member(request, member_id: int):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_invite_team or not permissions.merchant:
        return JsonResponse(
            {"error": "You do not have permission to edit team members."},
            status=403,
        )

    membership = get_object_or_404(
        MerchantTeamMember,
        pk=member_id,
        merchant=permissions.merchant,
    )

    if membership.role == MerchantTeamMember.Role.SUPERUSER:
        return JsonResponse(
            {"error": "The account owner cannot be edited."},
            status=400,
        )

    try:
        payload = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        payload = {}

    form = TeamMemberUpdateForm(payload, user=membership.user)
    if not form.is_valid():
        return JsonResponse({"errors": form.errors}, status=400)

    user = membership.user
    user.first_name = (form.cleaned_data.get("first_name") or "").strip()
    user.last_name = (form.cleaned_data.get("last_name") or "").strip()
    user.email = form.cleaned_data["email"]
    user.save(update_fields=["first_name", "last_name", "email"])

    membership.role = form.cleaned_data["role"]
    membership.save(update_fields=["role"])

    return JsonResponse({"success": True})


@login_required
@require_POST
def delete_team_member(request, member_id: int):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_invite_team or not permissions.merchant:
        return JsonResponse({"error": "You do not have permission to remove team members."}, status=403)

    membership = get_object_or_404(
        MerchantTeamMember,
        pk=member_id,
        merchant=permissions.merchant,
    )

    if membership.role == MerchantTeamMember.Role.SUPERUSER:
        return JsonResponse({"error": "The account owner cannot be removed."}, status=400)

    if membership.user_id == request.user.id:
        return JsonResponse({"error": "You cannot remove your own account."}, status=400)

    membership.user.delete()

    return JsonResponse({"success": True})
