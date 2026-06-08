from django.urls import path

from .views import (
    connect_youtube,
    youtube_callback,
    youtube_disconnect,
    youtube_status,
    youtube_sync,
)


urlpatterns = [
    path("connect/", connect_youtube, name="connect_youtube"),
    path("callback/", youtube_callback, name="youtube_callback"),
    path("status/", youtube_status, name="youtube_status"),
    path("disconnect/", youtube_disconnect, name="youtube_disconnect"),
    path("sync/", youtube_sync, name="youtube_sync"),
]
