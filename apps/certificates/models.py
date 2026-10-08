import uuid

from django.conf import settings
from django.db import models


class Certificate(models.Model):
    enrollment = models.OneToOneField(
        "courses.Enrollment", on_delete=models.PROTECT, related_name="certificate"
    )
    # Ommaviy tekshirish havolasi uchun: ketma-ket ID emas, taxmin qilib bo'lmaydigan UUID
    uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    issued_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-issued_at"]

    def __str__(self) -> str:
        return str(self.uid)

    @property
    def url(self) -> str:
        return f"{settings.CERTIFICATE_BASE_URL}{self.uid}"
