from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db.models.fields.files import FieldFile
from django.http import FileResponse, Http404, HttpResponse
from PIL import Image, UnidentifiedImageError

from apps.errors import ServiceError

ALLOWED_IMAGE_FORMATS = {"JPEG", "PNG", "WEBP"}


def validate_image(file: UploadedFile, max_bytes: int) -> None:
    """Faqat haqiqiy JPG/PNG/WEBP (kengaytmaga emas, fayl ichiga qaraladi) va `max_bytes` gacha."""
    if file.size > max_bytes:
        raise ServiceError(
            f"Rasm {max_bytes // (1024 * 1024)} MB dan oshmasligi kerak.", code="file_too_large"
        )
    try:
        with Image.open(file) as image:
            image_format = image.format
            image.verify()
    except (UnidentifiedImageError, OSError, SyntaxError) as exc:
        raise ServiceError("Fayl rasm emas.", code="invalid_image") from exc
    finally:
        file.seek(0)
    if image_format not in ALLOWED_IMAGE_FORMATS:
        raise ServiceError("Faqat JPG, PNG yoki WEBP.", code="invalid_image")


def protected_file_response(file: FieldFile) -> HttpResponse:
    """Serve a private media file after the caller has checked permissions.

    Prod: Django only sets `X-Accel-Redirect`; Nginx streams the file from an `internal`
    location, so a gunicorn worker is not tied up. Dev: Django streams it itself.
    """
    if not file:
        raise Http404
    if settings.USE_X_ACCEL_REDIRECT:
        response = HttpResponse()
        response["X-Accel-Redirect"] = settings.PROTECTED_MEDIA_URL + file.name
        response["Content-Type"] = ""  # Nginx fayl kengaytmasidan aniqlaydi
    else:
        try:
            response = FileResponse(file.open("rb"))
        except FileNotFoundError as exc:
            raise Http404 from exc
    response["Cache-Control"] = "private, no-store"
    return response
