from dataclasses import dataclass
from typing import Optional

from accounts.models import CustomUser

from .models import MerchantTeamMember


@dataclass
class MerchantPermissions:
    user: CustomUser
    merchant: Optional[CustomUser]
    membership: Optional[MerchantTeamMember]
    role: Optional[str]
    can_view_dashboard: bool
    can_view_settings: bool
    can_edit_settings: bool
    can_manage_api: bool
    can_invite_team: bool
    can_view_team: bool
    can_modify_content: bool

    @property
    def role_label(self) -> str:
        if self.membership:
            return self.membership.get_role_display()
        if self.role == MerchantTeamMember.Role.SUPERUSER:
            return "Superuser"
        return ""

    @property
    def is_superuser(self) -> bool:
        return self.role == MerchantTeamMember.Role.SUPERUSER


def resolve_merchant_permissions(user: CustomUser) -> MerchantPermissions:
    membership = None
    merchant_user: Optional[CustomUser] = None
    role: Optional[str] = None

    if user.is_authenticated:
        membership = getattr(user, "merchant_team_membership", None)
        if membership is None:
            membership = (
                MerchantTeamMember.objects.filter(user=user)
                .select_related("merchant")
                .first()
            )
            if membership:
                setattr(user, "merchant_team_membership", membership)

        if membership:
            merchant_user = membership.merchant
            role = membership.role
        elif user.is_merchant:
            membership, _ = MerchantTeamMember.objects.get_or_create(
                user=user,
                defaults={
                    "merchant": user,
                    "role": MerchantTeamMember.Role.SUPERUSER,
                },
            )
            merchant_user = user
            role = membership.role

    can_view_dashboard = merchant_user is not None
    can_manage_api = role == MerchantTeamMember.Role.SUPERUSER
    can_view_settings = role in {
        MerchantTeamMember.Role.SUPERUSER,
        MerchantTeamMember.Role.ADMIN,
        MerchantTeamMember.Role.MEMBER,
    }
    can_edit_settings = role in {
        MerchantTeamMember.Role.SUPERUSER,
        MerchantTeamMember.Role.ADMIN,
    }
    can_invite_team = can_edit_settings
    can_view_team = can_view_settings
    can_modify_content = role in {
        MerchantTeamMember.Role.SUPERUSER,
        MerchantTeamMember.Role.ADMIN,
        MerchantTeamMember.Role.MEMBER,
    }

    return MerchantPermissions(
        user=user,
        merchant=merchant_user,
        membership=membership,
        role=role,
        can_view_dashboard=can_view_dashboard,
        can_view_settings=can_view_settings,
        can_edit_settings=can_edit_settings,
        can_manage_api=can_manage_api,
        can_invite_team=can_invite_team,
        can_view_team=can_view_team,
        can_modify_content=can_modify_content,
    )
