from decimal import Decimal, ROUND_HALF_UP
import json
import secrets

from django.contrib.auth.decorators import login_required
from django.db.models import Sum, Count
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
from shopify_app.shopify_client import ShopifyClient
from shopify_app import billing as shopify_billing
from shopify_app.oauth import normalise_shop_domain
from ledger.models import LedgerEntry, MerchantInvoice
from django.http import HttpResponseForbidden, JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from urllib.parse import urlparse, urlencode
from django.urls import reverse
from typing import Optional
from django.utils.text import slugify

from collect.models import AffiliateClick, ReferralVisit

from .access import resolve_merchant_permissions


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
        return render(request, "403.html", status=403)

    merchant_user = permissions.merchant
    merchant_meta = None
    if merchant_user is not None:
        try:
            merchant_meta = merchant_user.merchantmeta
        except MerchantMeta.DoesNotExist:
            merchant_meta = None

    if merchant_meta and merchant_meta.requires_shopify_oauth():
        shop_domain = normalise_shop_domain(merchant_meta.shopify_store_domain)
        if shop_domain:
            authorize_url = (
                f"{reverse('shopify_oauth_authorize')}?"
                f"{urlencode({'shop': shop_domain})}"
            )
            return redirect(authorize_url)

    balance = LedgerEntry.merchant_balance(merchant_user)
    entries = LedgerEntry.objects.filter(merchant=merchant_user).order_by('-timestamp')
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
    })


@login_required
def merchant_invoices(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return render(request, "403.html", status=403)

    merchant_user = permissions.merchant
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

    try:
        merchant_meta = merchant_user.merchantmeta
    except MerchantMeta.DoesNotExist:
        merchant_meta = None

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
        },
    )

@login_required
def merchant_items(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_dashboard:
        return render(request, "403.html", status=403)

    merchant_user = permissions.merchant

    shopify_items = []
    shopify_domain = ""
    merchant_meta = MerchantMeta.objects.filter(user=merchant_user).first()
    if (
        merchant_meta
        and merchant_meta.shopify_access_token
        and merchant_meta.shopify_store_domain
    ):
        shopify_domain = merchant_meta.shopify_store_domain
        client = ShopifyClient(
            merchant_meta.shopify_access_token, merchant_meta.shopify_store_domain
        )
        try:
            shopify_items = client.get_all_products()
        except Exception:
            shopify_items = []

    # Map shopify product IDs to existing groups so the template can disable
    # items that are already assigned to a group. This prevents merchants from
    # selecting items that are in another group before submitting the form.
    existing_items = (
        MerchantItem.objects.filter(merchant=merchant_user)
        .prefetch_related("groups")
    )
    item_group_map = {}
    for item in existing_items:
        group = item.groups.first()
        if group:
            item_group_map[item.shopify_product_id] = group
    for product in shopify_items:
        group = item_group_map.get(str(product["id"]))
        if group:
            product["existing_group_id"] = group.id
            product["existing_group_name"] = group.name

    if request.method == "POST":
        if not permissions.can_modify_content:
            return render(request, "403.html", status=403)
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
                product_map = {str(p["id"]): p for p in shopify_items}
                items_to_add = []
                conflicts = []
                for pid in selected_items:
                    product = product_map.get(pid)
                    if product:
                        item, _ = MerchantItem.objects.get_or_create(
                            merchant=merchant_user,
                            shopify_product_id=str(product["id"]),
                            defaults={
                                "title": product["title"],
                                "link": f"https://{shopify_domain}/products/{product['handle']}",
                            },
                        )
                        if item.groups.exclude(pk=group.pk).exists():
                            conflicts.append(item.title)
                        else:
                            items_to_add.append(item)
                if conflicts:
                    post_data = request.POST.dict()
                    post_data.pop("csrfmiddlewaretoken", None)
                    request.session["group_form_post"] = post_data
                    request.session["group_form_selected"] = selected_items
                    request.session["group_form_conflicts"] = conflicts
                    return redirect("merchant_items")
                else:
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
    return render(
        request,
        "merchants/items.html",
        {
            "merchant": merchant_user,
            "groups": groups,
            "group_form": group_form,
            "shopify_items": shopify_items,
            "shopify_domain": shopify_domain,
            "selected_shopify_items": selected_items,
            "group_modal_open": bool(group_form.errors),
            "permissions": permissions,
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
        MerchantCreatorLink.objects.filter(
            merchant=merchant_user, creator__id__in=creator_ids
        ).delete()

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
            MerchantCreatorLink.objects.filter(
                merchant=merchant_user, creator__id__in=creator_ids
            ).update(
                status=STATUS_ACTIVE if action == "activate" else STATUS_INACTIVE
            )
    return redirect("merchant_creators")


@login_required
def request_creator(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_modify_content:
        return HttpResponseForbidden()

    merchant_user = permissions.merchant
    if request.method == "POST":
        uuid = request.POST.get("creator_uuid", "").strip()
        if uuid:
            try:
                creator_meta = CreatorMeta.objects.get(uuid=uuid)
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
        return render(request, "403.html", status=403)

    merchant_user = permissions.merchant
    merchant_meta = getattr(merchant_user, "merchantmeta", None)

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
            "total_earnings": total_earnings,
            "monthly_earnings": monthly_earnings,
            "visits": visits,
            "conversions": conversions,
            "avg_earnings_per_visit": avg,
            "conversion_rate": conversion_rate,
        }

    active_creators = [build_creator_entry(link) for link in active_links]
    inactive_creators = [build_creator_entry(link) for link in inactive_links]
    pending_creators = [
        {
            "link_id": link.id,
            "creator_id": link.creator.id,
            "username": link.creator.username,
            "email": link.creator.email,
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
        },
    )


@login_required
def merchant_settings(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_view_settings:
        return render(request, "403.html", status=403)

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
                post_data["shopify_access_token"] = merchant_meta.shopify_access_token
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
        form.fields["shopify_access_token"].disabled = True
        form.fields["shopify_store_domain"].disabled = True

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
    })


@login_required
@require_POST
def start_shopify_billing(request):
    permissions = resolve_merchant_permissions(request.user)
    if not permissions.can_edit_settings:
        return JsonResponse({"error": "You do not have permission to update billing."}, status=403)

    merchant_user = permissions.merchant
    if merchant_user is None:
        return JsonResponse({"error": "No merchant account found."}, status=400)

    merchant_meta, _ = MerchantMeta.objects.get_or_create(user=merchant_user)

    if merchant_meta.business_type != MerchantMeta.BusinessType.SHOPIFY:
        return JsonResponse(
            {"error": "Shopify billing is not enabled for this merchant."},
            status=400,
        )

    if not merchant_meta.shopify_access_token or not merchant_meta.shopify_store_domain:
        return JsonResponse(
            {"error": "Shopify credentials are required before starting billing."},
            status=400,
        )

    return_url = request.build_absolute_uri(f"{reverse('merchant_settings')}?tab=billing")

    try:
        charge = shopify_billing.create_or_update_recurring_charge(
            merchant_meta,
            return_url=return_url,
        )
    except shopify_billing.ShopifyBillingError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    merchant_meta.refresh_from_db()

    payload = {
        "status": merchant_meta.shopify_billing_status,
        "charge_id": merchant_meta.shopify_recurring_charge_id,
        "confirmation_url": merchant_meta.shopify_billing_confirmation_url,
    }

    if charge and isinstance(charge, dict):
        payload["raw"] = charge

    return JsonResponse(payload)


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
