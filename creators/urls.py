from django.urls import path
from .views import (
    creator_earnings,
    creator_affiliate_companies,
    creator_affiliate_companies_data,
    creator_my_links,
    creator_profile,
    creator_settings,
    creator_support,
    creator_marketplace,
    creator_requests,
    creator_send_request,
    delete_affiliate_merchants,
    respond_request,
)

urlpatterns = [
    path('earnings/', creator_earnings, name='creator_earnings'),
    path('affiliate-companies/', creator_affiliate_companies, name='creator_affiliate_companies'),
    path('affiliate-companies/delete/', delete_affiliate_merchants, name='creator_delete_affiliations'),
    path('affiliate-companies/data/', creator_affiliate_companies_data, name='creator_affiliate_companies_data'),
    path('my-links/', creator_my_links, name='creator_my_links'),
    path('my-links/<int:merchant_id>/', creator_my_links, name='creator_my_links_merchant'),
    path('my-links/<int:merchant_id>/<int:group_id>/', creator_my_links, name='creator_my_links_group'),
    path('profile/', creator_profile, name='creator_profile'),
    path('settings/', creator_settings, name='creator_settings'),
    path('support/', creator_support, name='creator_support'),
    path('marketplace/', creator_marketplace, name='creator_marketplace'),
    path('requests/', creator_requests, name='creator_requests'),
    path('requests/send/', creator_send_request, name='creator_send_request'),
    path('respond-request/<int:link_id>/', respond_request, name='respond_request'),
]
