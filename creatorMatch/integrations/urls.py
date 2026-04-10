from django.urls import include, path

app_name = "integrations"

urlpatterns = [
    path("instagram/", include("creatorMatch.integrations.instagram.urls")),
]
