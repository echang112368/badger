from django.db import models

class RedirectLink(models.Model):
    short_code = models.CharField(max_length = 255, unique = True)
    destination_url = models.URLField()
    queryParam = models.CharField(max_length = 255)

    def __str__(self):
        return f"{self.short_code}, {self.destination_url}, {self.queryParam}"