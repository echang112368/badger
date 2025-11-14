from urllib.parse import urlencode

from django.contrib.auth import authenticate, login, logout
from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import (
    CustomLoginForm,
    BusinessSignUpForm,
    CreatorSignUpForm,
    UserSignUpForm,
)
from merchants.models import MerchantTeamMember, MerchantMeta
from shopify_app.oauth import normalise_shop_domain

def custom_login_view(request):
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            membership = getattr(user, "merchant_team_membership", None)
            if membership is None:
                membership = MerchantTeamMember.objects.filter(user=user).first()

            if user.is_merchant or membership:
                merchant_account = user if user.is_merchant else membership.merchant
                meta = getattr(merchant_account, "merchantmeta", None)

                if isinstance(meta, MerchantMeta) and meta.requires_shopify_oauth():
                    shop_domain = normalise_shop_domain(meta.shopify_store_domain)
                    if shop_domain:
                        authorize_url = (
                            f"{reverse('shopify_oauth_authorize')}?"
                            f"{urlencode({'shop': shop_domain})}"
                        )
                        return redirect(authorize_url)

                return redirect('merchant_dashboard')
            elif user.is_creator:
                return redirect('creator_earnings')
            else:
                return redirect('user_dashboard')
    else:
        form = CustomLoginForm()

    return render(request, 'accounts/login.html', {'form': form})

def signup_choice_view(request):
    return render(request, 'accounts/signup_choice.html')

def business_signup_view(request):
    if request.method == 'POST':
        form = BusinessSignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_merchant = True
            user.is_active = True
            user.save()
            return render(request, 'accounts/signup_success.html', {'user': user})
    else:
        form = BusinessSignUpForm()
    return render(request, 'accounts/business_signup.html', {'form': form})

def creator_signup_view(request):
    if request.method == 'POST':
        form = CreatorSignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_creator = True
            user.is_active = True
            user.save()
            return render(request, 'accounts/signup_success.html', {'user': user})
    else:
        form = CreatorSignUpForm()
    return render(request, 'accounts/creator_signup.html', {'form': form})


def user_signup_view(request):
    if request.method == 'POST':
        form = UserSignUpForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.is_active = True
            user.save()
            return render(request, 'accounts/signup_success.html', {'user': user})
    else:
        form = UserSignUpForm()
    return render(request, 'accounts/user_signup.html', {'form': form})


def logout_view(request):
    """Log out the current user and redirect to the login page."""
    logout(request)
    return redirect('login')
