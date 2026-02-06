from django.urls import path

from shopify_app.uninstall.views import app_uninstall_webhook

from . import views

urlpatterns = [
    path('', views.embedded_app_home, name='shopify_embedded_home'),
    path('oauth/authorize/', views.oauth_authorize, name='shopify_oauth_authorize'),
    path('oauth/callback/', views.oauth_callback, name='shopify_oauth_callback'),
    path('billing/return/', views.billing_return, name='shopify_billing_return'),
    path('create-discount/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
    path('app/uninstall/', app_uninstall_webhook, name='shopify_app_uninstall_webhook'),
]
