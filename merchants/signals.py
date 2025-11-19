from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import CustomUser
from .models import MerchantMeta, MerchantTeamMember

@receiver(post_save, sender=CustomUser)
def create_merchant_meta(sender, instance, created, **kwargs):
    if created and instance.is_merchant:
        MerchantMeta.objects.get_or_create(user=instance)
        MerchantTeamMember.objects.get_or_create(
            user=instance,
            defaults={
                "merchant": instance,
                "role": MerchantTeamMember.Role.SUPERUSER,
            },
        )


@receiver(post_save, sender=MerchantMeta)
def enforce_plan_creator(sender, instance, **kwargs):
    from links import services as link_services

    if instance.includes_badger_creator:
        link_services.ensure_automatic_creator_for_merchant(instance.user)
    else:
        link_services.remove_automatic_creator_for_merchant(instance.user)

