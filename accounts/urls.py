# accounts/urls.py
from django.urls import path
from .views import (
    custom_login_view,
    signup_choice_view,
    business_signup_view,
    creator_signup_view,
    user_signup_view,
    logout_view,
)

urlpatterns = [
    path('login/', custom_login_view, name='login'),
    path('signup/', signup_choice_view, name='signup_choice'),
    path('signup/business/', business_signup_view, name='business_signup'),
    path('signup/creator/', creator_signup_view, name='creator_signup'),
    path('signup/user/', user_signup_view, name='user_signup'),
    path('logout/', logout_view, name='logout'),
]
