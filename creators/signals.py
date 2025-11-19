from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import CustomUser
from .models import CreatorMeta

@receiver(post_save, sender=CustomUser)
def create_creator_meta(sender, instance, **kwargs):
    if instance.is_creator:
        CreatorMeta.objects.get_or_create(user=instance)
