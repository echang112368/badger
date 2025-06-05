from django.urls import path
from .views import creator_dashboard

urlpatterns = [
    path('dashboard/', creator_dashboard, name='creator_dashboard'),
]
