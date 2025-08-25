# your_app/urls.py
from django.urls import path
from .views import redirect_view
from .views import webhook_view, stripe_webhook_view, orders_create_webhook
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse

urlpatterns = [
    path('webhook/', webhook_view, name="webhook_view"),
    path('stripe-webhook/', stripe_webhook_view, name="stripe_webhook_view"),
    path('shopify/webhooks/orders-create/', orders_create_webhook, name="shopify_orders_create_webhook"),
    path('<str:short_code>/', redirect_view, name='redirect_view'),


]
