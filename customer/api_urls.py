from django.urls import path

from .api_views import LoginView, PointsView

urlpatterns = [
    path("login/", LoginView.as_view(), name="api_login"),
    path("points/<uuid:uuid>/", PointsView.as_view(), name="api_points"),
]

