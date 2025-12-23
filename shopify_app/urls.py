from django.urls import path
from . import views

urlpatterns = [
    path('', views.embedded_app_home, name='shopify_embedded_home'),
    path('shopify/install/', views.install, name='shopify_oauth_authorize'),
    path('shopify/oauth/callback/', views.oauth_callback, name='shopify_oauth_callback'),
    path('shopify/billing/return/', views.billing_return, name='shopify_billing_return'),
    path('shopify/webhooks/', views.webhook_receiver, name='shopify_webhooks'),
    path('shopify/discounts/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
]
