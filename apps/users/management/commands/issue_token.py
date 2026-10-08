from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import User


class Command(BaseCommand):
    help = "Swagger'da sinash uchun JWT access token (faqat DEBUG=True)."

    def add_arguments(self, parser) -> None:
        parser.add_argument("user", help="id, telefon yoki username")

    def handle(self, *args, user: str, **options) -> None:
        if not settings.DEBUG:
            raise CommandError("Faqat DEBUG=True rejimida ishlaydi.")
        lookup = Q(phone=user) | Q(username=user)
        if user.isdigit():
            lookup |= Q(pk=int(user))
        found = User.objects.filter(lookup).first()
        if found is None:
            raise CommandError("Foydalanuvchi topilmadi.")
        self.stdout.write(str(RefreshToken.for_user(found).access_token))
