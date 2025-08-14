from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.http import QueryDict
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.authtoken.models import Token

from .models import CustomerMeta


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(APIView):
    """Authenticate a user and return an auth token with profile data.

    The response includes the user's name and current points balance.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        json_package = request.data
        if isinstance(json_package, QueryDict):
            json_package = json_package.dict()
        print("LoginView received JSON package:", json_package)

        email = json_package.get("email") or json_package.get("username")
        password = json_package.get("password")

        if not email or not password:
            return Response(
                {"detail": "Username and password required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        User = get_user_model()
        try:
            user = User.objects.get(
                email=email, is_merchant=False, is_creator=False
            )
        except User.DoesNotExist:
            user = None

        if user is None or not user.check_password(password):
            return Response(
                {"detail": "Invalid credentials"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        token, _ = Token.objects.get_or_create(user=user)
        customer, _ = CustomerMeta.objects.get_or_create(user=user)

        return Response(
            {
                "token": token.key,
                "uuid": str(customer.uuid),
                "name": user.username,
                "points": customer.points,
                "json_package": json_package,
            }
        )

