from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomUser
from .models import CreatorMeta
from links.models import MerchantCreatorLink, STATUS_REQUESTED, STATUS_ACTIVE
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
            username="creator_view",
            password="pass",
            email="creator_view@example.com",
            is_creator=True,
        )
        self.merchant = CustomUser.objects.create_user(
            username="merchant_view",
            password="pass",
            email="merchant_view@example.com",
            is_merchant=True,
        )
        self.group = ItemGroup.objects.create(
            merchant=self.merchant, name="Group A", affiliate_percent=15
        )
        self.item = MerchantItem.objects.create(
            merchant=self.merchant,
            title="Product 1",
            link="https://example.com/p1",
        )
        self.group.items.add(self.item)
        MerchantCreatorLink.objects.create(
            merchant=self.merchant, creator=self.creator, status=STATUS_ACTIVE
        )
        self.client.force_login(self.creator)

    def test_groups_and_items_displayed(self):
        response = self.client.get(reverse("creator_affiliate_companies"))
        self.assertContains(response, "Group A")
        self.assertContains(response, "15")
        self.assertContains(response, "Product 1")

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
        expected_link = f"{self.item.link}?ref=badger:{self.creator_meta.uuid}"
        self.assertContains(response, expected_link)

