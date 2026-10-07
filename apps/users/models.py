from django.contrib.auth.models import AbstractUser


class User(AbstractUser):
    """Custom user. Fields (role, telegram_id, phone, language) are added in phase 2."""
