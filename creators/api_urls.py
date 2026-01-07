from django.urls import path

from .api_views import CreatorNameView

urlpatterns = [
    path('<uuid:uuid>/', CreatorNameView.as_view(), name='creator_name_api'),
]
