# Lesson24 — Backend (MVP)

Lesson24 online ta'lim platformasining backend qismi. Maqsad — **maksimal sodda, tez ishga tushadigan MVP**. Murakkablik faqat haqiqatan kerak bo'lganda qo'shiladi.

---

## 1. Texnologiyalar

| Qism | Tanlov |
|---|---|
| Til | Python 3.12 |
| Framework | Django 5 + Django REST Framework |
| Ma'lumotlar bazasi | PostgreSQL (lokal — kompyuterga o'rnatilgan, prod — cloud: Neon / Supabase / Railway) |
| Auth | Telegram bot + 6 xonali kod → JWT (`djangorestframework-simplejwt`) |
| Telegram bot | `aiogram` 3 (Django ORM bilan bitta loyihada) |
| To'lov | Qo'lda: karta orqali o'tkazma + chek skrinshoti, admin tasdiqlaydi |
| Konfiguratsiya | `django-environ` (`.env` fayl) |
| API hujjat | `drf-spectacular` (Swagger: `/api/docs/`) |
| Server | Gunicorn + Nginx + systemd (virtualenv) |

**MVP'da ataylab yo'q:** Docker, Celery, Redis, Payme/Click integratsiyasi, microservice'lar, WebSocket. Login kodlari va boshqa vaqtinchalik ma'lumotlar PostgreSQL'da saqlanadi. Keyin kerak bo'lsa qo'shiladi.

---

## 2. Loyiha strukturasi

```
lesson24-backend/
├── config/                 # Django sozlamalari
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── apps/
│   ├── users/              # User, rollar, Telegram auth, ota-ona bog'lash
│   ├── courses/            # Kurs, dars, quiz, yozilish, progress
│   ├── payments/           # Buyurtma, chek skrinshoti, admin tasdig'i
│   └── certificates/       # Sertifikat yaratish va tekshirish
├── bot/
│   ├── main.py             # aiogram bot (alohida jarayon)
│   └── handlers.py
├── media/                  # yuklangan fayllar (chek skrinshotlari) — git'ga qo'shilmaydi
├── manage.py
├── requirements.txt
└── .env.example
```

Qoidalar:
- Faqat **4 ta app**. Yangi app faqat mavjudlariga sig'maydigan katta domen paydo bo'lganda yaratiladi.
- Har bir app ichida: `models.py`, `serializers.py`, `views.py`, `urls.py`, `permissions.py` (kerak bo'lsa), `admin.py`.
- Biznes logika murakkablashsa — `services.py` ga chiqariladi (view'lar yupqa bo'lsin).
- Bot `bot/` papkada, lekin Django ORM'dan foydalanadi (`django.setup()` orqali).

---

## 3. Ma'lumotlar bazasi

### Ulanish

```python
# settings.py
DATABASES = {"default": env.db("DATABASE_URL")}
DATABASES["default"]["CONN_MAX_AGE"] = 60
DATABASES["default"]["OPTIONS"] = {"sslmode": env("DB_SSLMODE", default="prefer")}
```

- Lokal: `DATABASE_URL=postgres://lesson24:lesson24@localhost:5432/lesson24`
- Cloud: provayder bergan URL, `DB_SSLMODE=require`
- Pooler'li ulanishda (Neon / Supabase pooler) `CONN_MAX_AGE=0` qo'yiladi.

### Modellar

**users**
```
User (AbstractUser)
  role            : student | parent | teacher | admin
  telegram_id     : BigInteger, unique
  phone           : str, unique
  first_name, last_name
  language        : uz | ru | en

LoginCode
  user            : FK User
  code            : str(6)
  expires_at      : datetime        # yaratilgandan +5 daqiqa
  is_used         : bool
  attempts        : int             # 5 ta xato urinishdan keyin bekor

ParentLinkCode
  student         : FK User
  code            : str(8)
  expires_at      : datetime        # +24 soat
  is_used         : bool

ParentLink
  parent          : FK User
  student         : FK User
  created_at
  unique_together : (parent, student)
```

**courses**
```
Course
  title, slug, description, cover
  teacher         : FK User (role=teacher)
  price           : int (so'm, maks 100 000)
  is_published    : bool

Lesson
  course          : FK Course
  order           : int
  title, description
  video_id        : str             # stream xizmatidagi ID (video Django'da saqlanmaydi)
  duration_min    : int
  materials       : JSON / file     # qo'shimcha resurslar
  is_free_preview : bool

Quiz / Question / Answer           # dars oxiridagi test
  Quiz            : FK Lesson
  Question        : FK Quiz, text
  Answer          : FK Question, text, is_correct

Enrollment
  student         : FK User
  course          : FK Course
  created_at, completed_at (null)
  unique_together : (student, course)

LessonProgress
  enrollment      : FK Enrollment
  lesson          : FK Lesson
  is_completed    : bool
  quiz_score      : int (null)
  completed_at
```

**payments**
```
Order
  user            : FK User         # to'lovchi (student yoki parent)
  student         : FK User         # kurs kimga ochiladi
  course          : FK Course
  amount          : int             # buyurtma paytidagi kurs narxi
  screenshot      : ImageField      # to'lov cheki
  status          : pending | approved | rejected
  reject_reason   : str (null)
  reviewed_by     : FK User (null)  # tasdiqlagan admin
  reviewed_at     : datetime (null)
  created_at
```

**certificates**
```
Certificate
  enrollment      : OneToOne Enrollment
  uid             : UUID            # ommaviy tekshirish havolasi uchun
  issued_at
```

---

## 4. Telegram orqali auth

### Oqim

```
1. Saytda "Telegram orqali kirish" → t.me/<bot>?start=login
2. Bot /start → "Kontaktni ulashish" tugmasi (telefon raqam)
3. Bot: telegram_id + phone bo'yicha User topadi yoki yaratadi (default role=student)
4. Bot 6 xonali kod yuboradi (LoginCode, 5 daqiqa amal qiladi)
5. Foydalanuvchi saytda /login sahifasida kodni kiritadi
6. POST /api/auth/verify/ { phone, code } → { access, refresh }
```

### Qoidalar
- Kod `secrets.randbelow` bilan generatsiya qilinadi (`random` emas).
- Kod **bir martalik**: ishlatilgach `is_used=True`.
- Har bir kod uchun maks. 5 urinish, keyin yangi kod so'raladi.
- Yangi kod so'ralganda eski faol kodlar bekor qilinadi.
- `/api/auth/verify/` endpointida DRF throttling (masalan, `5/min` IP bo'yicha).
- Access token — 30 daqiqa, refresh — 7 kun.

### Ota-ona ro'yxatdan o'tishi
Bot orqali ro'yxatdan o'tgan har bir foydalanuvchi — **student**. Ota-ona faqat farzandi yuborgan taklif havolasi
(`t.me/<bot>?start=p_<KOD>`) orqali kiradi: bot uni **parent** sifatida yaratadi va shu farzandga bog'laydi.
Rolni keyin faqat admin o'zgartiradi.

---

## 5. Ota-onani farzandga bog'lash

```
1. Student: POST /api/parents/link-code/        → { code: "A7K2Q9XM", link: "https://t.me/<bot>?start=p_A7K2Q9XM", expires_at }
2. Student havolani ota-onasiga yuboradi (share)
3. Ota-ona havolani ochadi → botda kontakt ulashadi → parent sifatida yaratiladi + ParentLink
   (allaqachon parent bo'lsa — faqat bog'lanadi; yoki saytda: POST /api/parents/link/ { code })
4. Parent:  GET  /api/parents/children/          → bog'langan farzandlar ro'yxati
5. Parent:  GET  /api/parents/children/{id}/progress/
```

- Kod bir martalik, 24 soat amal qiladi.
- Bitta ota-onaga bir nechta farzand bog'lanishi mumkin.
- Ota-ona faqat o'ziga bog'langan farzand ma'lumotini ko'radi (permission orqali tekshiriladi).

---

## 6. To'lov (qo'lda, skrinshot orqali)

### Oqim

```
1. Kurs sahifasida "Sotib olish" → GET /api/payments/info/
   → { card_number, card_holder, amount }   # .env dan olinadi
2. Foydalanuvchi kartaga pul o'tkazadi
3. POST /api/orders/  (multipart: course, student?, screenshot)
   → Order(status=pending)
4. Bot adminlar guruhiga xabar yuboradi: kim, qaysi kurs, summa + skrinshot
5. Admin Django Admin'da (yoki bot tugmasi orqali) "Tasdiqlash" / "Rad etish"
6. Tasdiqlansa → Enrollment yaratiladi, studentga bot orqali "Kurs ochildi" xabari
   Rad etilsa  → sababi bilan bot orqali xabar
```

### Qoidalar
- Tasdiqlash **bitta tranzaksiyada**: `Order.status=approved` + `Enrollment` yaratish (`services.approve_order`).
- Bir xil student + kurs uchun bir vaqtda faqat **bitta `pending`** buyurtma bo'lishi mumkin.
- Allaqachon yozilgan kursga buyurtma qabul qilinmaydi.
- Skrinshot: faqat rasm (jpg/png/webp), maks. 5 MB.
- Skrinshotlar `media/payments/` da saqlanadi va **ommaviy ochiq emas**: faqat admin ko'ra oladi (Nginx orqali to'g'ridan-to'g'ri berilmaydi).
- `student` maydoni: student o'zi sotib olsa — o'zi; parent sotib olsa — bog'langan farzandlaridan biri (tekshiriladi).
- Django Admin'da `Order` ro'yxati: `pending` lar tepada, skrinshot preview, `approve` / `reject` action'lari.

Keyinchalik Payme/Click qo'shilganda `Order` modeli saqlanib qoladi — faqat `provider` va `provider_txn_id` maydonlari qo'shiladi.

---

## 7. API endpointlar (MVP)

Hammasi `/api/` prefiksi ostida.

| Metod | Endpoint | Kim | Tavsif |
|---|---|---|---|
| POST | `auth/verify/` | hamma | Kod → JWT |
| POST | `auth/refresh/` | hamma | Token yangilash |
| GET/PATCH | `me/` | auth | O'z profili |
| GET | `courses/` | hamma | Nashr qilingan kurslar |
| GET | `courses/{slug}/` | hamma | Kurs va darslar ro'yxati |
| GET | `lessons/{id}/` | yozilgan student | Dars + imzolangan video URL |
| POST | `lessons/{id}/complete/` | yozilgan student | Darsni tugatish |
| POST | `lessons/{id}/quiz/` | yozilgan student | Test javoblarini topshirish |
| GET | `my/courses/` | student | Mening kurslarim + progress |
| GET | `payments/info/` | auth | Karta raqami va summa |
| POST | `orders/` | student, parent | Skrinshot bilan buyurtma |
| GET | `my/orders/` | student, parent | Buyurtmalarim va holati |
| POST | `parents/link-code/` | student | Bog'lash kodi |
| POST | `parents/link/` | parent | Kod orqali bog'lanish |
| GET | `parents/children/` | parent | Farzandlar |
| GET | `parents/children/{id}/progress/` | parent | Farzand progressi |
| GET | `my/certificates/` | student | Sertifikatlarim |
| GET | `certificates/{uid}/` | hamma | Sertifikatni tekshirish (QR) |

Admin va teacher kontentni va buyurtmalarni MVP'da **Django Admin** orqali boshqaradi — alohida admin panel yozilmaydi.

---

## 8. Ruxsatlar (permissions)

`apps/users/permissions.py` da:

```python
class IsStudent(BasePermission): ...


class IsParent(BasePermission): ...


class IsTeacher(BasePermission): ...


class IsEnrolled(BasePermission): ...  # darsga kirish uchun


class IsParentOfStudent(BasePermission): ...  # farzand ma'lumotiga kirish uchun
```

- Teacher Django Admin'da faqat **o'z kurslari**ni ko'radi (`get_queryset` orqali filtrlanadi).
- Buyurtmalarni faqat admin ko'radi va tasdiqlaydi.
- Pullik darsga faqat `Enrollment` mavjud bo'lsa kiriladi (`is_free_preview` darslar bundan mustasno).

---

## 9. Video

- Video fayllar Django serverida **saqlanmaydi va berilmaydi**.
- Stream xizmatiga (Bunny Stream / Cloudflare Stream) yuklanadi, bazada faqat `video_id` turadi.
- `GET /api/lessons/{id}/` qisqa muddatli **imzolangan URL** qaytaradi (HLS).

---

## 10. Kurs tugashi va sertifikat

- Kurs tugadi = barcha darslar `is_completed=True` va har bir quiz `quiz_score >= 60%`.
- Oxirgi dars tugatilganda `services.py` dagi `complete_course_if_done(enrollment)` chaqiriladi:
  - `Enrollment.completed_at` belgilanadi
  - `Certificate` yaratiladi
  - Bot orqali student (va bog'langan ota-onaga) xabar yuboriladi
- Sertifikat sahifasi: `https://lesson24.uz/certificate/{uid}` — QR kod shu havolaga olib boradi.

---

## 11. Muhit o'zgaruvchilari (`.env.example`)

```
DEBUG=False
SECRET_KEY=change-me
ALLOWED_HOSTS=api.lesson24.uz,localhost,127.0.0.1
CORS_ALLOWED_ORIGINS=https://lesson24.uz,http://localhost:3000

DATABASE_URL=postgres://lesson24:lesson24@localhost:5432/lesson24
DB_SSLMODE=prefer

TELEGRAM_BOT_TOKEN=
TELEGRAM_BOT_USERNAME=
TELEGRAM_ADMIN_CHAT_ID=          # yangi buyurtmalar shu guruhga keladi

PAYMENT_CARD_NUMBER=
PAYMENT_CARD_HOLDER=

VIDEO_PROVIDER_API_KEY=
VIDEO_SIGNING_KEY=
```

`.env` hech qachon git'ga qo'shilmaydi.

---

## 12. Ishga tushirish (Docker'siz)

### Lokal

```bash
# 1. PostgreSQL'da baza yaratish
sudo -u postgres psql -c "CREATE USER lesson24 WITH PASSWORD 'lesson24';"
sudo -u postgres psql -c "CREATE DATABASE lesson24 OWNER lesson24;"

# 2. Virtual muhit
python3.12 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Sozlash
cp .env.example .env            # qiymatlarni to'ldiring
python manage.py migrate
python manage.py createsuperuser

# 4. Ishga tushirish (ikki terminalda)
python manage.py runserver
python bot/main.py
```

### Prod (VPS)

- Baza: cloud PostgreSQL (`DATABASE_URL` + `DB_SSLMODE=require`) yoki serverdagi PostgreSQL.
- Ikki systemd servis:
  - `lesson24-web` → `gunicorn config.wsgi:application -b 127.0.0.1:8000 -w 3`
  - `lesson24-bot` → `python bot/main.py`
- Nginx: `api.lesson24.uz` → `127.0.0.1:8000`, `/static/` to'g'ridan-to'g'ri; `/media/payments/` **ochiq berilmaydi**.
- HTTPS: certbot.
- Deploy: `git pull && pip install -r requirements.txt && python manage.py migrate && python manage.py collectstatic --noinput && sudo systemctl restart lesson24-web lesson24-bot`

---

## 13. Kod yozish qoidalari

- View'lar — DRF `ViewSet` / `GenericAPIView`; biznes logika `services.py` da.
- Har bir FK va tez-tez filtrlanadigan maydon uchun indeks; N+1 dan qochish uchun `select_related` / `prefetch_related`.
- Pul — butun son (so'm), `float` ishlatilmaydi.
- Vaqt — `timezone.now()`, `USE_TZ=True`, `TIME_ZONE="Asia/Tashkent"`.
- Type hint'lar va qisqa docstring'lar.
- Har bir muhim oqim uchun test: auth, ota-ona bog'lash, buyurtma tasdiqlash, kurs tugashi.
- Formatlash: `ruff` + `ruff format`.

---

## 14. MVP checklist

- [ ] Loyiha skeleti, virtualenv, PostgreSQL ulanishi
- [ ] `users`: User modeli, rollar
- [ ] Telegram bot: kontakt olish, login kodi
- [ ] `auth/verify/` + JWT
- [ ] `courses`: Course, Lesson, Quiz, Enrollment, LessonProgress + Django Admin
- [ ] Dars sahifasi + imzolangan video URL
- [ ] Ota-ona bog'lash va progress endpointlari
- [ ] Skrinshot orqali buyurtma + admin tasdig'i + bot xabarlari
- [ ] Sertifikat yaratish va tekshirish
- [ ] Swagger hujjati
- [ ] Prod deploy (Gunicorn + Nginx + systemd)

**Keyingi bosqichlar (MVP'dan keyin):** Payme/Click, AI yordamchi, jonli darslar (Google Meet), uy vazifa va teacher tekshiruvi, Arena challenge, analitika, Docker, Celery + Redis.
