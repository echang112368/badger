from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import CustomUser


@receiver(post_save, sender=CustomUser)
def sync_automatic_creator_links(sender, instance, created, **kwargs):
    """Ensure automatic creator assignments stay in sync with user state."""

    auto_changed = getattr(instance, "_automatic_creator_changed", False)
    if not auto_changed:
        return

    from merchants.models import MerchantMeta
    from links import services as link_services

    if instance.automatic_creator:
        metas = (
            MerchantMeta.objects.filter(
                plan_type=MerchantMeta.PlanType.BADGER_EXTENSION
            )
            .select_related("user")
            .all()
        )
        link_services.ensure_automatic_creator_for_merchants(
            (meta.user for meta in metas), creator=instance
        )
    else:
        link_services.remove_creator_from_all_merchants(instance)
