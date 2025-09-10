from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from links.models import MerchantCreatorLink, STATUS_REQUESTED, STATUS_ACTIVE
from creators.models import CreatorMeta
from .forms import MerchantSettingsForm, ItemGroupForm
from accounts.forms import UserNameForm
from .models import MerchantItem, MerchantMeta, ItemGroup
from shopify_app.shopify_client import ShopifyClient
from ledger.models import LedgerEntry, MerchantInvoice
from django.http import HttpResponseForbidden, JsonResponse, QueryDict
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from urllib.parse import urlparse


def _normalize_domain(domain: str) -> str:
    if not domain:
        return ""
    parsed = urlparse(domain if "://" in domain else f"//{domain}")
    host = parsed.netloc or parsed.path
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host


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
    balance = LedgerEntry.merchant_balance(request.user)
    entries = LedgerEntry.objects.filter(merchant=request.user).order_by('-timestamp')
    invoices = (
        MerchantInvoice.objects.filter(merchant=request.user)
        .order_by('-created_at')
    )

    from ledger.invoices import update_invoice_status
    for invoice in invoices:
        update_invoice_status(invoice)

    return render(request, 'merchants/dashboard.html', {
        'merchant': request.user,
        'balance': balance,
        'ledger_entries': entries,
        'invoices': invoices,
    })

@login_required
def merchant_items(request):
    if not request.user.is_merchant:
        return render(request, "403.html")

    shopify_items = []
    shopify_domain = ""
    merchant_meta = MerchantMeta.objects.filter(user=request.user).first()
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
        MerchantItem.objects.filter(merchant=request.user)
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
        if request.POST.get("form_type") == "group":
            group_id = request.POST.get("group_id")
            group = (
                ItemGroup.objects.filter(id=group_id, merchant=request.user).first()
                if group_id
                else None
            )
            group_form = ItemGroupForm(
                request.POST, instance=group, merchant=request.user, prefix="group"
            )
            selected_items = request.POST.getlist("shopify_items")
            if group_form.is_valid():
                group = group_form.save(commit=False)
                group.merchant = request.user
                product_map = {str(p["id"]): p for p in shopify_items}
                items_to_add = []
                conflicts = []
                for pid in selected_items:
                    product = product_map.get(pid)
                    if product:
                        item, _ = MerchantItem.objects.get_or_create(
                            merchant=request.user,
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
            group = ItemGroup.objects.filter(id=group_id, merchant=request.user).first()
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
            ItemGroup.objects.filter(id=group_id, merchant=request.user).first()
            if group_id
            else None
        )
        group_form = ItemGroupForm(qdict, instance=group, merchant=request.user, prefix="group")
        group_form.is_valid()
        if conflicts:
            group_form.add_error(
                None,
                "The following items are already in another group: " + ", ".join(conflicts),
            )
    else:
        group_form = ItemGroupForm(merchant=request.user, prefix="group")
        selected_items = []

    groups = ItemGroup.objects.filter(merchant=request.user).prefetch_related("items")
    return render(
        request,
        "merchants/items.html",
        {
            "groups": groups,
            "group_form": group_form,
            "shopify_items": shopify_items,
            "shopify_domain": shopify_domain,
            "selected_shopify_items": selected_items,
            "group_modal_open": bool(group_form.errors),
        },
    )

@login_required
def delete_item(request):
    if request.method == 'POST':
        ids = request.POST.getlist('selected_items')
        for item_id in ids:
            item = MerchantItem.objects.filter(id=item_id, merchant=request.user).first()
            if item:
                item.delete()
        return redirect('merchant_dashboard')
    return HttpResponseForbidden()

@login_required
def delete_creators(request):
    if request.method == "POST":
        creator_ids = request.POST.getlist("selected_creators")
        MerchantCreatorLink.objects.filter(
            merchant=request.user, creator__id__in=creator_ids
        ).delete()

    return redirect("merchant_creators")


@login_required
def request_creator(request):
    if request.method == "POST":
        uuid = request.POST.get("creator_uuid", "").strip()
        if uuid:
            try:
                creator_meta = CreatorMeta.objects.get(uuid=uuid)
                link, created = MerchantCreatorLink.objects.get_or_create(
                    merchant=request.user,
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
    active_links = MerchantCreatorLink.objects.filter(
        merchant=request.user, status=STATUS_ACTIVE
    )
    creators = [link.creator for link in active_links]
    pending_links = MerchantCreatorLink.objects.filter(
        merchant=request.user, status=STATUS_REQUESTED
    )

    return render(request, 'merchants/creators.html', {
        'merchant': request.user,
        'creators': creators,
        'pending_links': pending_links,
    })


@login_required
def merchant_settings(request):
    merchant_meta, _ = MerchantMeta.objects.get_or_create(user=request.user)
    if request.method == "POST":
        form = MerchantSettingsForm(request.POST, instance=merchant_meta)
        user_form = UserNameForm(request.POST, instance=request.user)
        if form.is_valid() and user_form.is_valid():
            form.save()
            user_form.save()
            return redirect('merchant_settings')
    else:
        form = MerchantSettingsForm(instance=merchant_meta)
        user_form = UserNameForm(instance=request.user)

    return render(request, 'merchants/settings.html', {
        'merchant': request.user,
        'merchant_meta': merchant_meta,
        'settings_form': form,
        'user_form': user_form,
    })
