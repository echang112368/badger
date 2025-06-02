# your_app/urls.py
from django.urls import path
from .views import redirect_view
from .views import webhook_view

urlpatterns = [
    path('<str:short_code>/', redirect_view, name='redirect_view'),
    path('webhook/', webhook_view, name='webhook_view'),

]