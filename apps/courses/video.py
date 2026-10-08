"""Video URLs for lessons. Provider is chosen by `VIDEO_PROVIDER` in `.env`:

- `drive` — Google Drive "anyone with the link" file, shown in the Drive embed player (iframe).
  The link itself is public: we only hand it to enrolled students (`GET lessons/{id}/`), so it is
  not in the catalog, but a technical user can still find it in the browser.
- `bunny` — Bunny Stream signed HLS URL that expires (`VIDEO_URL_TTL`), for hls.js.

Only `Lesson.video_id` is stored, so switching providers needs no data model change.
"""

import base64
import hashlib
import re
import time
from urllib.parse import quote

from django.conf import settings

DRIVE = "drive"
BUNNY = "bunny"

# Drive fayl ID si: harf, raqam, "-" va "_" (amalda 25+ belgi)
_DRIVE_ID = r"[A-Za-z0-9_-]{20,}"
_DRIVE_URL_PATTERNS = [
    re.compile(rf"/file/d/({_DRIVE_ID})"),  # .../file/d/<id>/view?usp=sharing
    re.compile(rf"[?&]id=({_DRIVE_ID})"),  # .../open?id=<id>, .../uc?id=<id>
]


def parse_drive_id(value: str) -> str | None:
    """Drive havolasi yoki toza ID → fayl ID. Tanilmasa `None`."""
    value = (value or "").strip()
    if not value:
        return None
    if "drive.google.com" in value or "docs.google.com" in value:
        for pattern in _DRIVE_URL_PATTERNS:
            if match := pattern.search(value):
                return match.group(1)
        return None
    return value if re.fullmatch(_DRIVE_ID, value) else None


def normalize_video_id(value: str) -> str:
    """Admin kiritgan qiymat → saqlanadigan `video_id`. Drive: havola ham, ID ham qabul qilinadi.

    Raises `ValueError` if a Drive value is not recognised.
    """
    value = (value or "").strip()
    if not value or settings.VIDEO_PROVIDER != DRIVE:
        return value
    file_id = parse_drive_id(value)
    if file_id is None:
        raise ValueError("Google Drive havolasi yoki fayl ID si tanilmadi.")
    return file_id


def drive_embed_url(file_id: str) -> str:
    return f"https://drive.google.com/file/d/{file_id}/preview"


def video_type() -> str:
    """Frontend uchun: `iframe` (Drive player) yoki `hls` (hls.js)."""
    return "iframe" if settings.VIDEO_PROVIDER == DRIVE else "hls"


def lesson_video_url(video_id: str) -> str | None:
    if not video_id:
        return None
    if settings.VIDEO_PROVIDER == DRIVE:
        return drive_embed_url(video_id)
    return signed_hls_url(video_id)


def signed_hls_url(video_id: str, *, now: float | None = None) -> str | None:
    """Bunny: short-lived playlist URL (directory token), or `None` if not configured.

    The token covers the whole `/<video_id>/` directory, so the player can fetch the playlist
    and every segment with the same URL prefix. Pure function — no HTTP call.
    """
    key, host = settings.VIDEO_SIGNING_KEY, settings.VIDEO_CDN_HOSTNAME
    if not (video_id and key and host):
        return None
    ttl = settings.VIDEO_URL_TTL
    # Muddat TTL/2 oynasiga yaxlitlanadi: bir oyna ichida URL bir xil — brauzer/CDN keshi ishlaydi
    window = max(ttl // 2, 1)
    # natija: now+ttl <= expires < now+ttl+window
    expires = (int(now if now is not None else time.time()) // window + 1) * window + ttl
    token_path = f"/{video_id}/"
    hashable = f"{key}{token_path}{expires}token_path={token_path}"
    token = (
        base64.urlsafe_b64encode(hashlib.sha256(hashable.encode()).digest()).decode().rstrip("=")
    )
    return (
        f"https://{host}/bcdn_token={token}&token_path={quote(token_path, safe='')}"
        f"&expires={expires}{token_path}playlist.m3u8"
    )
