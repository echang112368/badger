from django.urls import path
from .views import merchant_dashboard
from . import views

urlpatterns = [
    path('store-id/', views.store_id_lookup, name='merchant_store_id'),
    path('delete-creators/', views.delete_creators, name='delete_creators'),
    path('request-creator/', views.request_creator, name='request_creator'),
    path('delete-item/', views.delete_item, name='delete_items'),
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
    path('items/', views.merchant_items, name='merchant_items'),
    path('creators/', views.merchant_creators, name='merchant_creators'),
    path('settings/', views.merchant_settings, name='merchant_settings'),
]
