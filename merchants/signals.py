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

