from rest_framework import permissions, status, exceptions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.contrib.auth import logout, login as auth_login
from django.contrib.auth.views import LoginView as DjangoLoginView
from django.views import View
from django.shortcuts import render

from .serializers import EmailTokenObtainPairSerializer, UserSerializer
from .forms import CustomLoginForm, UserSignUpForm


class APILoginView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except exceptions.AuthenticationFailed:
            return Response({'error': 'invalid_credentials'}, status=status.HTTP_401_UNAUTHORIZED)


class WebLoginView(DjangoLoginView):
    template_name = "accounts/login.html"
    authentication_form = CustomLoginForm


class SignupView(View):
    form_class = UserSignUpForm
    template_name = "accounts/user_signup.html"

    def get(self, request):
        form = self.form_class()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = self.form_class(request.POST)
        if form.is_valid():
            user = form.save()
            auth_login(request, user)
            return render(request, "accounts/signup_success.html", {"user": user})
        return render(request, self.template_name, {"form": form})


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


def logout_view(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)
