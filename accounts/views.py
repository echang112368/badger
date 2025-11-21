from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth import get_user_model, login, logout
from django.shortcuts import render, redirect
from django.urls import reverse
from .forms import (
    CustomLoginForm,
    BusinessSignUpForm,
    CreatorSignUpForm,
    UserSignUpForm,
    EmailVerificationForm,
)
from merchants.models import MerchantTeamMember, MerchantMeta
from shopify_app.oauth import normalise_shop_domain
from accounts.services.verification import (
    get_user_by_pk,
    needs_email_verification,
    send_user_verification_email,
    verify_user_code,
)

User = get_user_model()


def _remember_verification_user(request, user: User) -> None:
    request.session["verification_user_id"] = user.pk


def _get_verification_user(request):
    if request.user.is_authenticated:
        return request.user
    return get_user_by_pk(request.session.get("verification_user_id"))

def custom_login_view(request):
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            if needs_email_verification(user):
                send_user_verification_email(user)
                _remember_verification_user(request, user)
                messages.info(
                    request,
                    "Please verify your email before continuing. "
                    "We've sent a verification code to your inbox.",
                )
                return redirect('verify_email')

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
            send_user_verification_email(user)
            _remember_verification_user(request, user)
            messages.success(
                request,
                "Account created! Check your inbox for a verification code.",
            )
            return redirect('verify_email')
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
            send_user_verification_email(user)
            _remember_verification_user(request, user)
            messages.success(
                request,
                "Account created! Check your inbox for a verification code.",
            )
            return redirect('verify_email')
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
            send_user_verification_email(user)
            _remember_verification_user(request, user)
            messages.success(
                request,
                "Account created! Check your inbox for a verification code.",
            )
            return redirect('verify_email')
    else:
        form = UserSignUpForm()
    return render(request, 'accounts/user_signup.html', {'form': form})


def logout_view(request):
    """Log out the current user and redirect to the login page."""
    logout(request)
    return redirect('login')


def verify_email_view(request):
    """Allow a user to confirm their email with a 6-digit code."""

    user = _get_verification_user(request)
    if not user:
        messages.error(request, "We couldn't find an account to verify. Please log in.")
        return redirect('login')

    if user.email_verified:
        messages.info(request, "Your email is already verified. Please log in to continue.")
        return redirect('login')

    form = EmailVerificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if verify_user_code(user, form.cleaned_data["code"]):
            request.session.pop("verification_user_id", None)
            messages.success(request, "Email verified! You can now log in.")
            logout(request)
            return redirect('login')
        messages.error(request, "That code was not correct. Please try again or resend.")

    return render(
        request,
        'accounts/verify_email.html',
        {
            'form': form,
            'email': user.email,
        },
    )


def resend_verification_email_view(request):
    """Resend a new verification email for the current session user."""

    user = _get_verification_user(request)
    if not user:
        messages.error(request, "We couldn't find an account to verify. Please log in.")
        return redirect('login')

    send_user_verification_email(user, regenerate=True)
    messages.success(request, "A new verification code has been sent to your email.")
    return redirect('verify_email')
