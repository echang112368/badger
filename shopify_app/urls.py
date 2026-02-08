from django.urls import path

from shopify_app.gdpr.views import (
    customers_data_request_webhook,
    customers_redact_webhook,
    shop_redact_webhook,
)
from shopify_app.uninstall.views import app_uninstall_webhook

from . import views

urlpatterns = [
    path('', views.embedded_app_home, name='shopify_embedded_home'),
    path('oauth/authorize/', views.oauth_authorize, name='shopify_oauth_authorize'),
    path('oauth/callback/', views.oauth_callback, name='shopify_oauth_callback'),
    path('billing/return/', views.billing_return, name='shopify_billing_return'),
    path('create-discount/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
    path('app/uninstall/', app_uninstall_webhook, name='shopify_app_uninstall_webhook'),
    path(
        'webhooks/customers/data-request/',
        customers_data_request_webhook,
        name='shopify_customers_data_request_webhook',
    ),
    path(
        'webhooks/customers/redact/',
        customers_redact_webhook,
        name='shopify_customers_redact_webhook',
    ),
    path(
        'webhooks/shop/redact/',
        shop_redact_webhook,
        name='shopify_shop_redact_webhook',
    ),
]
