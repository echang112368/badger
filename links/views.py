from django.contrib.auth.decorators import login_required
from django.forms import modelformset_factory
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_GET

from links.forms import MerchantCreatorLinkForm
from links.models import MerchantCreatorLink
from links.services import get_automatic_creator_user


@login_required
@require_GET
def link_summary(request):
    user = request.user
    if user.is_merchant:
        links = MerchantCreatorLink.objects.filter(merchant=user)
        counterparties = [{'creator': link.creator.username, 'status': link.status} for link in links]
    elif user.is_creator:
        links = MerchantCreatorLink.objects.filter(creator=user)
        counterparties = [{'merchant': link.merchant.username, 'status': link.status} for link in links]
    else:
        counterparties = []

    return JsonResponse({'linked_users': counterparties})

@login_required
def merchant_edit_creators(request):
    if not request.user.is_merchant:
        return redirect('forbidden')

    CreatorLinkFormSet = modelformset_factory(
        MerchantCreatorLink,
        form=MerchantCreatorLinkForm,
        extra=1,
        can_delete=True
    )

    queryset = MerchantCreatorLink.objects.filter(merchant=request.user)

    if request.method == 'POST':
        formset = CreatorLinkFormSet(request.POST, queryset=queryset)
        if formset.is_valid():
            instances = formset.save(commit=False)
            for obj in instances:
                obj.merchant = request.user  # auto-assign merchant
                obj.save()
            auto_creator = get_automatic_creator_user()
            for obj in formset.deleted_objects:
                if auto_creator and obj.creator_id == auto_creator.id:
                    continue
                obj.delete()
            return redirect('merchant_dashboard')  # or refresh page
    else:
        formset = CreatorLinkFormSet(queryset=queryset)

    return render(request, 'merchants/edit_creators.html', {'formset': formset})
