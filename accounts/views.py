from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from .forms import CustomLoginForm

def custom_login_view(request):
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if user.is_merchant:
                return print('merchant_dashboard')
            elif user.is_creator:
                return print('creator_dashboard')
            else:
                return print('default_dashboard')
    else:
        form = CustomLoginForm()

    return render(request, 'accounts/login.html', {'form': form})