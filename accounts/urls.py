# accounts/urls.py
from django.urls import path, reverse_lazy
from django.contrib.auth import views as auth_views
from .forms import DevFriendlyPasswordResetForm
from .views import (
    custom_login_view,
    signup_choice_view,
    business_signup_view,
    creator_signup_view,
    user_signup_view,
    logout_view,
    verify_email_view,
    resend_verification_email_view,
)

urlpatterns = [
    path('login/', custom_login_view, name='login'),
    path('signup/', signup_choice_view, name='signup_choice'),
    path('signup/business/', business_signup_view, name='business_signup'),
    path('signup/creator/', creator_signup_view, name='creator_signup'),
    path('signup/user/', user_signup_view, name='user_signup'),
    path('logout/', logout_view, name='logout'),
    path('verify-email/', verify_email_view, name='verify_email'),
    path('verify-email/resend/', resend_verification_email_view, name='resend_verification'),
    path(
        "password-reset/",
        auth_views.PasswordResetView.as_view(
            template_name="accounts/password_reset_form.html",
            email_template_name="accounts/emails/password_reset_email.txt",
            subject_template_name="accounts/emails/password_reset_subject.txt",
            html_email_template_name="accounts/emails/password_reset_email.html",
            success_url=reverse_lazy("password_reset_done"),
            form_class=DevFriendlyPasswordResetForm,
        ),
        name="password_reset",
    ),
    path(
        "password-reset/done/",
        auth_views.PasswordResetDoneView.as_view(
            template_name="accounts/password_reset_done.html"
        ),
        name="password_reset_done",
    ),
    path(
        "reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(
            template_name="accounts/password_reset_confirm.html",
            success_url=reverse_lazy("password_reset_complete"),
        ),
        name="password_reset_confirm",
    ),
    path(
        "reset/done/",
        auth_views.PasswordResetCompleteView.as_view(
            template_name="accounts/password_reset_complete.html"
        ),
        name="password_reset_complete",
    ),
]
