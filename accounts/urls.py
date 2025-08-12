from django.urls import path
from .views import WebLoginView, SignupView

urlpatterns = [
    path("login/", WebLoginView.as_view(), name="login"),
    path("signup/", SignupView.as_view(), name="signup"),
]
