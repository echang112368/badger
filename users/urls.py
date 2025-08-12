from django.urls import path
from .views import user_dashboard, user_settings


urlpatterns = [
    path('', user_dashboard, name='user_dashboard'),
    path('settings/', user_settings, name='user_settings'),
]
