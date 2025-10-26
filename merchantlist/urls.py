from django.urls import path

from .views import merchant_list, merchant_meta

urlpatterns = [
    path("api/merchant_meta/", merchant_meta, name="merchant_meta"),
    path("api/merchant_list/", merchant_list, name="merchant_list"),
]
