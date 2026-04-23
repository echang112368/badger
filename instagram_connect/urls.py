from django.urls import path

from .views import (
    connect_instagram,
    instagram_callback,
    instagram_disconnect,
    instagram_status,
    instagram_sync,
)


urlpatterns = [
    path("connect/", connect_instagram, name="connect_instagram"),
    path("callback/", instagram_callback, name="instagram_callback"),
    path("status/", instagram_status, name="instagram_status"),
    path("disconnect/", instagram_disconnect, name="instagram_disconnect"),
    path("sync/", instagram_sync, name="instagram_sync"),
]
