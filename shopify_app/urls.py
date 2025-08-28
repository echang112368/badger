from django.urls import path
from . import views

urlpatterns = [
    path('create-discount/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
]
