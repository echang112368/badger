from rest_framework import permissions, status, exceptions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.contrib.auth import logout

from .serializers import EmailOrUsernameTokenObtainPairSerializer, UserSerializer


class LoginView(TokenObtainPairView):
    serializer_class = EmailOrUsernameTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        try:
            return super().post(request, *args, **kwargs)
        except exceptions.AuthenticationFailed:
            return Response({'error': 'invalid_credentials'}, status=status.HTTP_401_UNAUTHORIZED)


class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)


def logout_view(request):
    logout(request)
    return Response(status=status.HTTP_204_NO_CONTENT)
