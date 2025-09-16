from decimal import Decimal

from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import CreatorMeta
from ledger.models import LedgerEntry
from links.models import (
    MerchantCreatorLink,
    STATUS_ACTIVE,
    STATUS_INACTIVE,
    STATUS_REQUESTED,
)
from merchants.models import MerchantMeta, ItemGroup, MerchantItem
from rest_framework_simplejwt.tokens import RefreshToken


class CreatorSettingsTests(TestCase):
    def test_settings_displays_uuid(self):
        user = CustomUser.objects.create_user(
            username="creator_uuid",
            password="pass",
            email="creator_uuid@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_settings"))
        creator_meta = CreatorMeta.objects.get(user=user)
        self.assertContains(response, str(creator_meta.uuid))

    def test_settings_displays_email(self):
        user = CustomUser.objects.create_user(
            username="creator",
            password="pass",
            email="creator@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_settings"))
        self.assertContains(response, user.email)

    def test_settings_displays_password(self):
        user = CustomUser.objects.create_user(
            username="creator2",
            password="pass123",
            email="creator2@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.get(reverse("creator_settings"))
        self.assertContains(response, user.password)

    def test_settings_updates_name(self):
        user = CustomUser.objects.create_user(
            username="creator3",
            password="pass123",
            email="creator3@example.com",
            is_creator=True,
        )
        self.client.force_login(user)
        response = self.client.post(
            reverse("creator_settings"),
            {"first_name": "New", "last_name": "Name", "paypal_email": ""},
        )
        self.assertRedirects(response, reverse("creator_settings"))
        user.refresh_from_db()
        self.assertEqual(user.last_name, "Name")


class CreatorNameAPITests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username="creator_api",
            password="pass123",
            email="creator_api@example.com",
            first_name="Api",
            last_name="Tester",
            is_creator=True,
        )
        self.meta = CreatorMeta.objects.get(user=self.user)
        self.token = str(RefreshToken.for_user(self.user).access_token)

    def test_requires_authentication(self):
        url = reverse("creator_name_api", kwargs={"uuid": self.meta.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 401)

    def test_returns_creator_name(self):
        url = reverse("creator_name_api", kwargs={"uuid": self.meta.uuid})
        response = self.client.get(url, HTTP_AUTHORIZATION=f"Bearer {self.token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("name"), "Api Tester")


class CreatorRequestTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_req",
            password="pass",
            email="creator_req@example.com",
            is_creator=True,
        )
        self.merchant = CustomUser.objects.create_user(
            username="merchant_req",
            password="pass",
            email="merchant_req@example.com",
            is_merchant=True,
        )

    def test_pending_request_displayed(self):
        MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(response, self.merchant.username)
        self.assertContains(response, "Accept")

    def test_accept_request(self):
        link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("respond_request", args=[link.id]), {"action": "accept"}
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        link.refresh_from_db()
        self.assertEqual(link.status, STATUS_ACTIVE)

    def test_decline_request(self):
        link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_REQUESTED,
        )
        self.client.force_login(self.creator)
        response = self.client.post(
            reverse("respond_request", args=[link.id]), {"action": "decline"}
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        self.assertFalse(
            MerchantCreatorLink.objects.filter(id=link.id).exists()
        )


class CreatorAffiliateCompaniesViewTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_aff_view",
            password="pass",
            email="creator_aff_view@example.com",
            is_creator=True,
        )
        self.merchant = CustomUser.objects.create_user(
            username="merchant_aff_view",
            password="pass",
            email="merchant_aff_view@example.com",
            is_merchant=True,
        )
        self.merchant_meta = MerchantMeta.objects.get(user=self.merchant)
        self.merchant_meta.company_name = "Merchant Aff LLC"
        self.merchant_meta.save()
        self.link = MerchantCreatorLink.objects.create(
            merchant=self.merchant,
            creator=self.creator,
            status=STATUS_ACTIVE,
        )
        self.client.force_login(self.creator)

    def test_displays_company_metrics(self):
        LedgerEntry.objects.create(
            creator=self.creator,
            merchant=self.merchant,
            amount=Decimal("42.50"),
            entry_type="commission",
        )
        response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(response, "Merchant Aff LLC")
        self.assertContains(response, "Monthly Earnings")
        self.assertContains(response, "Avg. Per Click")
        self.assertContains(response, "$42.50")

    def test_inactive_company_lists_under_inactive_tab(self):
        self.link.status = STATUS_INACTIVE
        self.link.save()
        response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(response, "No active companies found.")
        self.assertContains(response, "Merchant Aff LLC")

    def test_delete_affiliate_company(self):
        response = self.client.post(
            reverse("creator_delete_affiliations"),
            {"selected_links": [str(self.link.id)]},
        )
        self.assertRedirects(response, reverse("creator_affiliate_companies"))
        self.assertFalse(
            MerchantCreatorLink.objects.filter(id=self.link.id).exists()
        )


class CreatorLinksTests(TestCase):
    def setUp(self):
        self.creator = CustomUser.objects.create_user(
            username="creator_links",
            password="pass",
            email="creator_links@example.com",
            is_creator=True,
        )
        self.creator_meta = CreatorMeta.objects.get(user=self.creator)
        self.merchant = CustomUser.objects.create_user(
            username="merchant_links",
            password="pass",
            email="merchant_links@example.com",
            is_merchant=True,
        )
        self.merchant_meta = MerchantMeta.objects.get(user=self.merchant)
        self.merchant_meta.company_name = "Nike"
        self.merchant_meta.save()
        self.group = ItemGroup.objects.create(
            merchant=self.merchant, name="Group 1", affiliate_percent=10
        )
        self.item = MerchantItem.objects.create(
            merchant=self.merchant,
            title="Shoe",
            link="https://example.com/shoe",
            shopify_product_id="222",
        )
        self.group.items.add(self.item)
        MerchantCreatorLink.objects.create(
            merchant=self.merchant, creator=self.creator, status=STATUS_ACTIVE
        )
        self.client.force_login(self.creator)

    def test_company_list(self):
        response = self.client.get(reverse("creator_my_links"))
        self.assertContains(response, "Nike")

    def test_group_list(self):
        url = reverse("creator_my_links_merchant", args=[self.merchant.id])
        response = self.client.get(url)
        self.assertContains(response, "Group 1")
        self.assertContains(response, "10")

    def test_item_list_includes_affiliate_link(self):
        url = reverse(
            "creator_my_links_group", args=[self.merchant.id, self.group.id]
        )
        response = self.client.get(url)
        expected_link = (
            f"{self.item.link}?ref=badger:{self.creator_meta.uuid}&item_id={self.item.shopify_product_id}"
        )
        self.assertContains(response, expected_link.replace("&", "&amp;"))

    def test_search_items_by_name_and_id(self):
        other = MerchantItem.objects.create(
            merchant=self.merchant,
            title="Hat",
            link="https://example.com/hat",
            shopify_product_id="333",
        )
        self.group.items.add(other)
        url = reverse("creator_my_links_group", args=[self.merchant.id, self.group.id])
        response = self.client.get(url, {"q": "Hat"})
        self.assertContains(response, "Hat")
        self.assertNotContains(response, "Shoe")
        response = self.client.get(url, {"q": other.shopify_product_id})
        self.assertContains(response, "Hat")
        self.assertNotContains(response, "Shoe")

