from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.db import models
from decimal import Decimal
from ledger.models import LedgerEntry
from .models import CustomerMeta
from .utils import get_points_balance, get_level_progress
from accounts.forms import UserNameForm


@login_required
def user_dashboard(request):
    user = request.user
    points_balance = get_points_balance(user)
    level_progress = get_level_progress(points_balance)
    redemption_value = points_balance / Decimal("600")
    lifetime_points = points_balance
    lifetime_savings = (
        LedgerEntry.objects.filter(
            creator=user,
            entry_type=LedgerEntry.EntryType.SAVINGS,
        ).aggregate(total=models.Sum("amount"))["total"]
        or Decimal("0")
    )
    since_year = user.date_joined.year

    ledger_entries = (
        LedgerEntry.objects.filter(creator=user, entry_type="points")
        .select_related("merchant__merchantmeta")
        .order_by("-timestamp")
    )

    transactions = []
    for entry in ledger_entries:
        if entry.merchant and hasattr(entry.merchant, "merchantmeta"):
            company = entry.merchant.merchantmeta.company_name
        else:
            company = ""
        transactions.append(
            {
                "transaction_date": entry.timestamp.date(),
                "company": company,
                "points": int(entry.amount),
                "amount_usd": (entry.amount / Decimal("600")).quantize(
                    Decimal("0.01")
                ),
            }
        )

    context = {
        'points_balance': points_balance,
        'redemption_value': redemption_value,
        'lifetime_points': lifetime_points,
        'lifetime_savings': lifetime_savings,
        'since_year': since_year,
        'level_progress': level_progress,
        'transactions': transactions,
    }
    return render(request, 'customer/dashboard.html', context)


@login_required
def user_settings(request):
    customer_meta, _ = CustomerMeta.objects.get_or_create(user=request.user)
    if request.method == 'POST':
        user_form = UserNameForm(request.POST, instance=request.user)
        if user_form.is_valid():
            user_form.save()
            return redirect('user_settings')
    else:
        user_form = UserNameForm(instance=request.user)
    return render(
        request,
        'customer/settings.html',
        {'customer_meta': customer_meta, 'customer': request.user, 'user_form': user_form},
    )
