from django.urls import path

from .api_views import CreatorNameView, SearchAPIView

urlpatterns = [
    path('<uuid:uuid>/', CreatorNameView.as_view(), name='creator_name_api'),
    path('search/', SearchAPIView.as_view(), name='creator_search_api'),
]
