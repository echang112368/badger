from django.urls import path
from .views import merchant_dashboard
from . import views

urlpatterns = [
    path('store-id/', views.store_id_lookup, name='merchant_store_id'),
    path('delete-creators/', views.delete_creators, name='delete_creators'),
    path('update-creator-status/', views.update_creator_status, name='update_creator_status'),
    path('request-creator/', views.request_creator, name='request_creator'),
    path('delete-item/', views.delete_item, name='delete_items'),
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
    path('invoices/', views.merchant_invoices, name='merchant_invoices'),
    path('items/', views.merchant_items, name='merchant_items'),
    path('creators/', views.merchant_creators, name='merchant_creators'),
    path('requests/', views.merchant_requests, name='merchant_requests'),
    path('requests/<int:request_id>/update/', views.merchant_update_request, name='merchant_update_request'),
    path('marketplace/', views.merchant_marketplace, name='merchant_marketplace'),
    path('team/<int:member_id>/update/', views.update_team_member, name='update_team_member'),
    path('team/<int:member_id>/delete/', views.delete_team_member, name='delete_team_member'),
    path('settings/', views.merchant_settings, name='merchant_settings'),
    path('settings/shopify/start-billing/', views.start_shopify_billing, name='merchant_start_shopify_billing'),
    path('settings/shopify/status/', views.refresh_shopify_billing_status, name='merchant_refresh_shopify_billing_status'),
]
