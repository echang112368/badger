from django.urls import path
from . import views

urlpatterns = [
    path('oauth/authorize/', views.oauth_authorize, name='shopify_oauth_authorize'),
    path('oauth/callback/', views.oauth_callback, name='shopify_oauth_callback'),
    path('create-discount/<uuid:merchant_uuid>/', views.create_discount, name='create_discount'),
]
