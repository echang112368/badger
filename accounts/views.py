from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
from .forms import CustomLoginForm, BusinessSignUpForm, CreatorSignUpForm
from verify_email.email_handler import ActivationMailManager
from django.http import HttpResponse


def custom_login_view(request):
    if request.method == 'POST':
        form = CustomLoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)

            if user.is_merchant:
                return redirect('merchant_dashboard')
            elif user.is_creator:
                return redirect('creator_dashboard')
            else:
                return HttpResponse('default_dashboard')
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
            ActivationMailManager.send_verification_link(inactive_user=user, request=request)
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
            ActivationMailManager.send_verification_link(inactive_user=user, request=request)
            return render(request, 'accounts/signup_success.html', {'user': user})
    else:
        form = CreatorSignUpForm()
    return render(request, 'accounts/creator_signup.html', {'form': form})