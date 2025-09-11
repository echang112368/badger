from django.contrib.auth import get_user_model
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt
from django.http import QueryDict
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework_simplejwt.tokens import RefreshToken

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
                {"error": "Invalid credentials"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh = RefreshToken.for_user(user)
        customer, _ = CustomerMeta.objects.get_or_create(user=user)

        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "uuid": str(customer.uuid),
                "name": f"{user.first_name} {user.last_name}".strip(),
                "points": get_points_balance(user),
                "json_package": json_package,
            }
        )


@method_decorator(csrf_exempt, name="dispatch")
class PointsView(APIView):
    """Return the current points balance for a customer by UUID."""

    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request, uuid):
        try:
            customer = CustomerMeta.objects.get(uuid=uuid)
        except CustomerMeta.DoesNotExist:
            return Response(
                {"detail": "Customer not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        return Response(
            {
                "uuid": str(customer.uuid),
                "points": get_points_balance(customer.user),
            }
        )

