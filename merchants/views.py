from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from links.models import MerchantCreatorLink
from .addItem_forms import MerchantItemForm
from .models import MerchantItem
from django.http import HttpResponseForbidden


@login_required
def merchant_dashboard(request):
    links = MerchantCreatorLink.objects.filter(merchant=request.user)
    creators = [link.creator for link in links]
    items = MerchantItem.objects.filter(merchant=request.user)

    return render(request, 'merchants/dashboard.html', {
        'merchant': request.user,
        'creators': creators,
        'items': items,
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