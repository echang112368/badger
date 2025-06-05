from django.urls import path
from .views import merchant_dashboard
from . import views

urlpatterns = [
    path('delete-item/', views.delete_item, name='delete_items'),
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
    path('add-item/', views.add_item, name='add_item'),
]
