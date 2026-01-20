import secrets

from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.http import QueryDict
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken, AccessToken
from rest_framework_simplejwt.exceptions import TokenError

from .models import CustomerMeta
from .utils import get_points_balance


@method_decorator(csrf_exempt, name="dispatch")
class LoginView(APIView):
    """Authenticate a user and return an auth token with profile data.

    The response includes the user's full name.
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
                {"detail": "Email and password required."},
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
                {"error": "Invalid credentials"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh = RefreshToken.for_user(user)
        customer, _ = CustomerMeta.objects.get_or_create(user=user)

        full_name = user.get_full_name().strip() or user.first_name

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "uuid": str(customer.uuid),
                "name": full_name,
                "points": get_points_balance(user),
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class CustomerPointsView(APIView):
    """Return the loyalty points balance for a customer."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        json_package = request.data
        if isinstance(json_package, QueryDict):
            json_package = json_package.dict()

        refresh_token = json_package.get("refresh")
        access_token = json_package.get("access")
        customer_uuid = json_package.get("uuid")

        if not refresh_token or not customer_uuid:
            return Response(
                {"detail": "Refresh token and uuid are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        access_user_id = None
        if access_token:
            try:
                access = AccessToken(access_token)
            except TokenError:
                access = None
            else:
                access_user_id = access.get("user_id")

        try:
            token = RefreshToken(refresh_token)
        except TokenError:
            return Response(
                {"detail": "Invalid refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_id = token.get("user_id")
        if not user_id:
            return Response(
                {"detail": "Token missing user information."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if access_user_id and access_user_id != user_id:
            return Response(
                {"detail": "Access token does not match refresh token."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        User = get_user_model()
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response(
                {"detail": "User not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            customer = CustomerMeta.objects.get(uuid=customer_uuid, user=user)
        except CustomerMeta.DoesNotExist:
            return Response(
                {"detail": "Invalid customer uuid."},
                status=status.HTTP_404_NOT_FOUND,
            )

        new_refresh = RefreshToken.for_user(user)
        new_refresh.payload["nonce"] = secrets.token_hex(8)
        new_access = AccessToken(payload={"user_id": user.id, "nonce": secrets.token_hex(8)})

        return Response(
            {
                "uuid": str(customer.uuid),
                "points": get_points_balance(user),
                "access": str(new_access),
                "refresh": str(new_refresh),
            }
        )
