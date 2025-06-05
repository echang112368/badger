# merchants/urls.py

from django.urls import path
from .views import merchant_dashboard

urlpatterns = [
    path('dashboard/', merchant_dashboard, name='merchant_dashboard'),
]

