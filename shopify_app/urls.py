from django.urls import path
from . import views

urlpatterns = [
    path('oauth/callback/', views.oauth_callback, name='shopify_oauth_callback'),
    path('create-discount/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
]
