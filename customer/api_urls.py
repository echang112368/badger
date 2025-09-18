from django.urls import path

from .api_views import LoginView, CustomerPointsView

urlpatterns = [
    path("login/", LoginView.as_view(), name="api_login"),
    path("points/", CustomerPointsView.as_view(), name="api_points"),
]

