from django.urls import path
from .views import merchant_dashboard
from . import views

urlpatterns = [
    path('delete-creators/', views.delete_creators, name='delete_creators'),
    path('request-creator/', views.request_creator, name='request_creator'),
    path('merchant/edit-creators/', views.merchant_edit_creators, name='merchant_edit_creators'),
    path('delete-item/', views.delete_item, name='delete_items'),
    path('update-commission/', views.update_commission, name='update_commission'),
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
    path('add-item/', views.add_item, name='add_item'),
]
