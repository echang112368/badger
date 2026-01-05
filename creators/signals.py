from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import CustomUser
from .models import CreatorMeta

@receiver(post_save, sender=CustomUser)
def create_creator_meta(sender, instance, created, **kwargs):
    if created and instance.is_creator:
        CreatorMeta.objects.get_or_create(
            user=instance,
            defaults={"display_name": instance.username},
        )
    elif instance.is_creator:
        meta, _ = CreatorMeta.objects.get_or_create(user=instance)
        if not meta.display_name:
            meta.display_name = instance.username
            meta.save(update_fields=["display_name"])
