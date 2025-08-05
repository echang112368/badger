from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from links.models import MerchantCreatorLink, STATUS_REQUESTED, STATUS_ACTIVE
from creators.models import CreatorMeta
from .forms import MerchantItemForm, MerchantSettingsForm
from .models import MerchantItem, MerchantMeta
from ledger.models import LedgerEntry, MerchantInvoice
from django.http import HttpResponseForbidden, JsonResponse
from django.views.decorators.csrf import csrf_exempt


@csrf_exempt
def store_id_lookup(request):
    domain = request.GET.get("domain", "").strip()
    if not domain:
        response = JsonResponse({"error": "domain parameter required"}, status=400)
    else:
        meta = MerchantMeta.objects.filter(shopify_store_domain=domain).first()
        if meta:
            response = JsonResponse({"storeID": str(meta.uuid)})
        else:
            response = JsonResponse({"error": "merchant not found"}, status=404)
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

    if request.method == "POST":
        item_id = request.POST.get("item_id")
        if item_id:
            item = MerchantItem.objects.filter(id=item_id, merchant=request.user).first()
            form = MerchantItemForm(request.POST, instance=item, prefix="edit")
        else:
            form = MerchantItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.merchant = request.user
            item.save()
            return redirect("merchant_items")
    else:
        form = MerchantItemForm()

    items = MerchantItem.objects.filter(merchant=request.user)
    edit_form = MerchantItemForm(prefix="edit")
    return render(
        request,
        "merchants/items.html",
        {"form": form, "items": items, "edit_form": edit_form},
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
        if form.is_valid():
            form.save()
            return redirect('merchant_settings')
    else:
        form = MerchantSettingsForm(instance=merchant_meta)

    return render(request, 'merchants/settings.html', {
        'merchant': request.user,
        'merchant_meta': merchant_meta,
        'settings_form': form,
    })
