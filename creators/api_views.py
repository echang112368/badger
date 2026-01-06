from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from .models import CreatorMeta


class CreatorNameView(APIView):
    """Return the full name for a creator identified by UUID.

    Requires a valid JWT access token.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, uuid):
        meta = get_object_or_404(CreatorMeta, uuid=uuid)
        user = meta.user
        name = f"{user.first_name} {user.last_name}".strip()
        return Response({"name": name})
