from django.urls import path
from . import views

urlpatterns = [
    path('settings/', views.shopify_settings, name='shopify_settings'),
    path('script.js', views.referral_script, name='shopify_referral_script'),
    path('order-webhook/', views.order_webhook, name='shopify_order_webhook'),
]
