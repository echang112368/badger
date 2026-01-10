from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.shortcuts import get_object_or_404

from .search import SearchService

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


class SearchAPIView(APIView):
    """Search across marketplace models using deterministic intent rules."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        query = request.GET.get("q", "")
        service = SearchService(query=query, user=request.user)
        results = service.search()
        return Response(results)
