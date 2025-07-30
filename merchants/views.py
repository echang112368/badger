from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from links.models import MerchantCreatorLink
from .forms import MerchantItemForm, MerchantMetaForm
from .models import MerchantItem, MerchantMeta
from ledger.models import LedgerEntry, MerchantInvoice
from django.http import HttpResponseForbidden, HttpResponse


@login_required
def merchant_dashboard(request):
    links = MerchantCreatorLink.objects.filter(merchant=request.user)
    creators = [link.creator for link in links]
    items = MerchantItem.objects.filter(merchant=request.user)
    merchant_meta = MerchantMeta.objects.filter(user=request.user).first()
    commission_form = MerchantMetaForm(instance=merchant_meta)
    balance = LedgerEntry.merchant_balance(request.user)
    entries = LedgerEntry.objects.filter(merchant=request.user).order_by('-timestamp')
    latest_invoice = (
        MerchantInvoice.objects.filter(merchant=request.user)
        .exclude(status='PAID')
        .order_by('-created_at')
        .first()
    )

    return render(request, 'merchants/dashboard.html', {
        'merchant': request.user,
        'merchant_meta': merchant_meta,
        'commission_form': commission_form,
        'creators': creators,
        'items': items,
        'balance': balance,
        'ledger_entries': entries,
        'latest_invoice': latest_invoice,
    })

@login_required
def add_item(request):
    if not request.user.is_merchant:
        return render(request, '403.html')

    if request.method == 'POST':
        form = MerchantItemForm(request.POST)
        if form.is_valid():
            item = form.save(commit=False)
            item.merchant = request.user  # attach item to the merchant
            item.save()
            return redirect('merchant_dashboard')  # redirect after adding
    else:
        form = MerchantItemForm()

    return render(request, 'merchants/add_item.html', {'form': form})

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

def delete_creators(request):
    if request.method == "POST":
        creator_ids = request.POST.getlist("selected_creators")
        MerchantCreatorLink.objects.filter(
            merchant=request.user, creator__id__in=creator_ids
        ).delete()

    return redirect("merchant_dashboard")


@login_required
def update_commission(request):
    merchant_meta = MerchantMeta.objects.filter(user=request.user).first()
    if not merchant_meta:
        return redirect("merchant_dashboard")

    if request.method == "POST":
        form = MerchantMetaForm(request.POST, instance=merchant_meta)
        if form.is_valid():
            form.save()

    return redirect("merchant_dashboard")

def merchant_edit_creators(request):
    return HttpResponse("Edit creators page (under construction)")
