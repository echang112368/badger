from django.urls import path

from creatorMatch.integrations.instagram import views

app_name = "instagram"

urlpatterns = [
    path("start/", views.start_oauth, name="start_oauth"),
    path("callback/", views.callback, name="callback"),
    path("status/", views.connection_status, name="status"),
    path("disconnect/", views.disconnect, name="disconnect"),
]
