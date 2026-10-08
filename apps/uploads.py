import uuid
from pathlib import PurePath

from django.utils import timezone
from django.utils.deconstruct import deconstructible


@deconstructible
class UploadTo:
    """`<prefix>/YYYY/MM/<uuid>.<ext>` — taxmin qilinmaydigan va to'qnashmaydigan fayl nomi."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix

    def __call__(self, instance, filename: str) -> str:
        ext = PurePath(filename).suffix.lower()[:10]
        return f"{self.prefix}/{timezone.now():%Y/%m}/{uuid.uuid4().hex}{ext}"

    def __eq__(self, other) -> bool:
        return isinstance(other, UploadTo) and other.prefix == self.prefix
