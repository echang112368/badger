from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def user_dashboard(request):
    user = request.user
    points_balance = 0
    redemption_value = points_balance / 100
    lifetime_points = points_balance
    lifetime_savings = 0
    since_year = user.date_joined.year
    transactions = []
    context = {
        'points_balance': points_balance,
        'redemption_value': redemption_value,
        'lifetime_points': lifetime_points,
        'lifetime_savings': lifetime_savings,
        'since_year': since_year,
        'transactions': transactions,
    }
    return render(request, 'users/dashboard.html', context)


@login_required
def user_settings(request):
    return render(request, 'users/settings.html')
