from django.urls import path
from .views import (
    creator_earnings,
    creator_affiliate_companies,
    creator_my_links,
    creator_settings,
    creator_support,
    respond_request,
)

urlpatterns = [
    path('earnings/', creator_earnings, name='creator_earnings'),
    path('affiliate-companies/', creator_affiliate_companies, name='creator_affiliate_companies'),
    path('my-links/', creator_my_links, name='creator_my_links'),
    path('my-links/<int:merchant_id>/', creator_my_links, name='creator_my_links_merchant'),
    path('my-links/<int:merchant_id>/<int:group_id>/', creator_my_links, name='creator_my_links_group'),
    path('settings/', creator_settings, name='creator_settings'),
    path('support/', creator_support, name='creator_support'),
    path('respond-request/<int:link_id>/', respond_request, name='respond_request'),
]
