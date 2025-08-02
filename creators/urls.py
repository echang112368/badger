from django.urls import path
from .views import creator_dashboard, respond_request

urlpatterns = [
    path('dashboard/', creator_dashboard, name='creator_dashboard'),
    path('respond-request/<int:link_id>/', respond_request, name='respond_request'),
]
