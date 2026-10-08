from django.core.management.base import BaseCommand

from apps.users.services import cleanup_codes


class Command(BaseCommand):
    help = "Muddati o'tgan va ishlatilgan bir martalik kodlarni o'chiradi (kuniga 1 marta)."

    def handle(self, *args, **options) -> None:
        self.stdout.write(f"O'chirildi: {cleanup_codes()}")
