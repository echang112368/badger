# your_app/urls.py
from django.urls import path

from .views import (
    orders_create_webhook,
    redirect_view,
    stripe_webhook_view,
    track_referral_visit,
    webhook_view,
)

urlpatterns = [
    path('track-visit/', track_referral_visit, name="collect_track_visit"),
    path('webhook/', webhook_view, name="webhook_view"),
    path('stripe-webhook/', stripe_webhook_view, name="stripe_webhook_view"),
    path('shopify/webhooks/orders-create/', orders_create_webhook, name="shopify_orders_create_webhook"),
    path('<str:short_code>/', redirect_view, name='redirect_view'),


]
