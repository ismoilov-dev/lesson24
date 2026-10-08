"""ImgBB: ommaviy rasmlar (kurs muqovalari) uchun xosting. Bazada faqat URL saqlanadi.

To'lov cheklari bu yerga YUBORILMAYDI — ular maxfiy va serverda yopiq saqlanadi.
"""

import logging

import httpx
from django.conf import settings
from django.core.files.uploadedfile import UploadedFile

from apps.errors import ServiceError
from apps.files import validate_image

logger = logging.getLogger(__name__)
UPLOAD_URL = "https://api.imgbb.com/1/upload"


def upload_image(file: UploadedFile, *, name: str = "") -> str:
    """Validate and upload an image to ImgBB; return its direct URL."""
    validate_image(file, settings.COURSE_COVER_MAX_BYTES)
    if not settings.IMGBB_API_KEY:
        raise ServiceError(
            "Rasm xostingi sozlanmagan (IMGBB_API_KEY).", code="image_upload_failed", status=503
        )
    data = {"name": name[:100]} if name else {}
    try:
        response = httpx.post(
            UPLOAD_URL,
            params={"key": settings.IMGBB_API_KEY},
            data=data,
            files={"image": (file.name, file.read())},
            timeout=20,
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("ImgBB upload failed: %s", type(exc).__name__)
        raise ServiceError(
            "Rasmni yuklab bo'lmadi, qayta urinib ko'ring.", code="image_upload_failed", status=502
        ) from exc
    finally:
        file.seek(0)
    url = (body.get("data") or {}).get("url") if body.get("success") else None
    if not url:
        logger.warning("ImgBB error: %s", (body.get("error") or {}).get("message"))
        raise ServiceError(
            "Rasmni yuklab bo'lmadi, qayta urinib ko'ring.", code="image_upload_failed", status=502
        )
    return url
