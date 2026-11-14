from django.db import models

class ParseJob(models.Model):
    title = models.CharField(max_length=120)
    code = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=32, default="待解析")
    source_uri = models.CharField(max_length=255)
    parsed = models.IntegerField(default=0)
    landed = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
