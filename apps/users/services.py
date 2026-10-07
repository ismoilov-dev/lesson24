"""User business logic: phone normalisation, bot users, one-time codes, parent links.

Shared by the API views and the Telegram bot (bot calls these via `sync_to_async`).
"""

import re
import secrets
import string

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F, Q
from django.utils import timezone

from apps.errors import ServiceError

from .models import OneTimeCode, ParentLink, User

INVITE_ALPHABET = "".join(c for c in string.ascii_uppercase + string.digits if c not in "0O1I")
INVALID_CODE = ServiceError("Kod noto'g'ri yoki muddati o'tgan.", code="invalid_code")


def normalize_phone(raw: str) -> str:
    """Return E.164 form (`+998901234567`). A bare 9-digit Uzbek number gets `998` prepended."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 9:
        digits = "998" + digits
    if not 10 <= len(digits) <= 15:
        raise ServiceError("Telefon raqam noto'g'ri.", code="invalid_phone")
    return "+" + digits


@transaction.atomic
def get_or_create_bot_user(
    *,
    telegram_id: int,
    phone: str,
    first_name: str = "",
    last_name: str = "",
    role: str = User.Role.STUDENT,
) -> tuple[User, bool]:
    """Find a user by Telegram ID or phone, or create one with `role`.

    A user pre-registered by an admin (phone only, e.g. a teacher) gets the Telegram ID attached
    and keeps their role.
    """
    phone = normalize_phone(phone)
    user = (
        User.objects.select_for_update().filter(telegram_id=telegram_id).first()
        or User.objects.select_for_update().filter(phone=phone, telegram_id__isnull=True).first()
    )
    if user is None:
        user = User(
            username=phone,
            telegram_id=telegram_id,
            phone=phone,
            first_name=first_name[:150],
            last_name=last_name[:150],
            role=role,
        )
        user.set_unusable_password()
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError as exc:
            raise ServiceError(
                "Bu telefon raqam boshqa Telegram akkauntga bog'langan.", code="phone_taken"
            ) from exc
        return user, True

    changed = []
    if user.telegram_id != telegram_id:
        user.telegram_id = telegram_id
        changed.append("telegram_id")
    if user.phone != phone:
        if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
            raise ServiceError(
                "Bu telefon raqam boshqa akkauntga bog'langan.", code="phone_taken"
            )
        user.phone = phone
        changed.append("phone")
    for field, value in (("first_name", first_name), ("last_name", last_name)):
        if value and not getattr(user, field):
            setattr(user, field, value[:150])
            changed.append(field)
    if changed:
        user.save(update_fields=changed)
    return user, False


def _generate_code(purpose: str) -> str:
    if purpose == OneTimeCode.Purpose.LOGIN:
        return f"{secrets.randbelow(1_000_000):06d}"
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(8))


@transaction.atomic
def issue_code(user: User, purpose: str) -> OneTimeCode:
    """Create a new code and expire the user's previous active codes of the same purpose."""
    now = timezone.now()
    OneTimeCode.objects.filter(
        user=user, purpose=purpose, used_at__isnull=True, expires_at__gt=now
    ).update(expires_at=now)
    return OneTimeCode.objects.create(
        user=user,
        purpose=purpose,
        code=_generate_code(purpose),
        expires_at=now + OneTimeCode.TTL[purpose],
    )


def verify_login_code(phone: str, code: str) -> User:
    """Check a login code. Wrong guesses are counted; after 5 the code is dead."""
    try:
        phone = normalize_phone(phone)
    except ServiceError:
        raise INVALID_CODE from None

    with transaction.atomic():
        # Qatorni bloklash: parallel so'rovlar urinishlar limitini aylanib o'tolmaydi
        otc = (
            OneTimeCode.objects.select_for_update()
            .select_related("user")
            .filter(
                user__phone=phone,
                user__is_active=True,
                purpose=OneTimeCode.Purpose.LOGIN,
                used_at__isnull=True,
                expires_at__gt=timezone.now(),
                attempts__lt=OneTimeCode.MAX_ATTEMPTS,
            )
            .order_by("-created_at")
            .first()
        )
        if otc is None:
            ok = False
        elif secrets.compare_digest(otc.code, (code or "").strip()):
            otc.used_at = timezone.now()
            otc.save(update_fields=["used_at"])
            ok = True
        else:
            OneTimeCode.objects.filter(pk=otc.pk).update(attempts=F("attempts") + 1)
            ok = False
    # Xato tranzaksiyadan tashqarida ko'tariladi — urinishlar soni rollback bo'lmaydi
    if not ok:
        raise INVALID_CODE
    return otc.user


def create_parent_invite(student: User) -> dict:
    """Issue an invite code a student shares with a parent."""
    if not student.is_student:
        raise ServiceError("Faqat student taklif yarata oladi.", code="not_student", status=403)
    otc = issue_code(student, OneTimeCode.Purpose.PARENT_LINK)
    return {
        "code": otc.code,
        "link": f"https://t.me/{settings.TELEGRAM_BOT_USERNAME}?start=p_{otc.code}",
        "expires_at": otc.expires_at,
    }


@transaction.atomic
def accept_parent_invite(parent: User, code: str) -> ParentLink:
    """Link `parent` to the student who issued `code`. The code is single-use."""
    if not parent.is_parent:
        raise ServiceError("Faqat ota-ona bog'lana oladi.", code="not_parent", status=403)
    otc = (
        OneTimeCode.objects.select_for_update()
        .select_related("user")
        .filter(
            purpose=OneTimeCode.Purpose.PARENT_LINK,
            code=(code or "").strip().upper(),
            used_at__isnull=True,
            expires_at__gt=timezone.now(),
        )
        .first()
    )
    if otc is None or not otc.user.is_student:
        raise INVALID_CODE
    otc.used_at = timezone.now()
    otc.save(update_fields=["used_at"])
    link, _ = ParentLink.objects.get_or_create(parent=parent, student=otc.user)
    return link


def cleanup_codes() -> int:
    """Delete codes that expired or were used more than a day ago. Returns deleted count."""
    cutoff = timezone.now() - OneTimeCode.TTL[OneTimeCode.Purpose.PARENT_LINK]
    deleted, _ = OneTimeCode.objects.filter(Q(expires_at__lt=cutoff) | Q(used_at__lt=cutoff)).delete()
    return deleted
