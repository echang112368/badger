from django.contrib.auth.decorators import login_required
from django.shortcuts import render


@login_required
def user_dashboard(request):
    return render(request, 'users/dashboard.html')


@login_required
def user_settings(request):
    return render(request, 'users/settings.html')
