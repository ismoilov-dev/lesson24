# Lesson24 — Backend

Django 5.2 + DRF + PostgreSQL + aiogram 3. To'liq spec: [`docs/BACKEND.md`](docs/BACKEND.md).
Docker, Celery, Redis ishlatilmaydi.

## Rollar

| Rol | Kim | Qanday paydo bo'ladi |
|---|---|---|
| `student` | O'quvchi | Botga `/start` → kontakt ulashadi |
| `parent` | Ota-ona | Farzand bergan havola (`t.me/<bot>?start=p_KOD`) orqali botga kiradi |
| `teacher` | O'z kurslarini joylaydigan o'qituvchi | Admin rol beradi (`admin/users/`), teacher botga shu raqam bilan kiradi |
| `admin` | Platforma jamoasi: barcha kontent, teacher kurslarini tekshirish, to'lovlar | Django Admin yoki `admin/users/` |

**Teacher kursi:** `draft` → `POST teacher/courses/{id}/submit/` → `pending` (admin guruhiga Telegram xabari)
→ admin `approve` (nashr) yoki `reject` (izoh bilan, teacher tuzatib qayta yuboradi). Teacher to'lovlarni ko'rmaydi.
Kontent boshqaruvi kodi umumiy (`apps/courses/manage_api.py`), admin/teacher faqat doira va action'lar bilan farqlanadi.

## Lokal ishga tushirish

```bash
# 1. PostgreSQL
createuser lesson24 --createdb -P          # parol: lesson24
createdb lesson24 -O lesson24

# 2. Virtual muhit
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt

# 3. Sozlash
cp .env.example .env    # DEBUG=True, SECRET_KEY, TELEGRAM_BOT_TOKEN ...
python manage.py migrate
python manage.py createcachetable
python manage.py createsuperuser

# 4. Ishga tushirish (ikki terminal)
python manage.py runserver
python bot/main.py
```

- Swagger: http://127.0.0.1:8000/api/docs/
- Django Admin: http://127.0.0.1:8000/admin/
- Swagger'da sinash uchun token (faqat `DEBUG=True`): `python manage.py issue_token <telefon|username|id>`

## Testlar va lint

```bash
python manage.py test
ruff check . && ruff format --check .
python manage.py spectacular --validate --fail-on-warn > /dev/null
```

## Asosiy oqimlar

**Kirish.** Botda `/start` → telefonni tugma orqali ulashish → 10 daqiqalik 6 xonali kod →
`POST /api/v1/auth/verify/ {phone, code}` → `{access, refresh, user}`. Access 30 daqiqa, refresh 7 kun
(`POST /api/v1/auth/refresh/`).

**Kursga yozilish.**
- Pullik kurs: `GET me/orders/payment-info/?course=<slug>` → kartaga o'tkazma → `POST me/orders/`
  (chek skrinshoti) → admin guruhiga Telegram xabari → admin tasdiqlaydi (bot tugmasi, Django Admin yoki
  `admin/orders/{id}/approve/`) → kurs ochiladi, studentga xabar.
- Admin qo'lda (naqd, sovg'a, Telegram'da yuborilgan chek): `POST admin/enrollments/ {student, course}`.
- Bepul kurs (narxi 0): `POST courses/<slug>/enroll/`.

**O'qish.** `GET lessons/{id}/` (imzolangan video URL, test savollari) → `POST lessons/{id}/complete/`
(test bo'lsa `answers` bilan, o'tish chegarasi 60%) → oxirgi dars tugaganda sertifikat va Telegram xabari.

**Ota-ona.** Student `POST me/parent-invites/` → havolani ota-onaga yuboradi → ota-ona botda ochadi →
`GET me/children/`, `GET me/children/{id}/progress/`.

## API tuzilmasi (`/api/v1/`)

| Prefiks | Kim |
|---|---|
| `auth/` | hamma |
| `me/` | kirgan foydalanuvchi: profil, kurslar, buyurtmalar, sertifikatlar, farzandlar |
| `courses/`, `certificates/{uid}/` | ommaviy |
| `lessons/` | yozilgan student (bepul darslar — hamma; admin — oldindan ko'rish) |
| `teacher/` | teacher: profil, o'z kurslari/darslari/testlari, tekshiruvga yuborish, o'quvchilar, statistika |
| `admin/` | admin: dashboard, foydalanuvchilar, buyurtmalar, kurslar/darslar/testlar (+ approve/reject), yozilishlar, ota-ona bog'lanishlari, sertifikatlar |

## Telegram

- `TELEGRAM_ADMIN_CHAT_ID` — yangi buyurtmalar keladigan guruh (ID odatda `-100...`). Bot shu guruhga qo'shilgan bo'lishi kerak.
- Tasdiqlash/rad etish tugmalarini faqat `role=admin` bosadi — admin botga bir marta kirgan bo'lishi kerak
  (`telegram_id` saqlanadi).
- Xabarlar tranzaksiya commit bo'lgandan keyin fon thread'ida yuboriladi; Telegram ishlamasa — faqat logga yoziladi.

## Kurs va dars joylash (admin)

1. **Kurs** — bitta forma (`POST /api/v1/admin/courses/`, `multipart/form-data`):
   `title`, `description`, `cover` (rasm fayli), `price` (+ ixtiyoriy `instructor_name`, `slug`, `is_published`).
   Rasm **ImgBB**'ga yuklanadi (`.env`: `IMGBB_API_KEY`), bazada faqat `cover_url` saqlanadi.
   Muqovani almashtirish — `PATCH` bilan yangi `cover`; olib tashlash — `{"cover_url": ""}`.
2. **Dars** — JSON (`POST /api/v1/admin/courses/{id}/lessons/`):
   `title`, `video_url` (Google Drive havolasi), `description`, `duration_min`, `is_free_preview`, `materials`.
3. Nashr qilish: `PATCH /api/v1/admin/courses/{id}/ {"is_published": true}`.

To'lov cheklari ImgBB'ga **yuborilmaydi** — ular maxfiy, serverda yopiq saqlanadi.

## Video

Provayder `.env` dagi `VIDEO_PROVIDER` bilan tanlanadi (bazada faqat `Lesson.video_id`):

**`drive` (hozirgi)** — Google Drive:
1. Videoni Drive'ga yuklang → **Share** → **Anyone with the link** (Viewer).
2. Havolani darsning `video_url` maydoniga to'liq yopishtiring (API) yoki Django Admin'dagi
   «Video (Google Drive havolasi)» maydoniga — backend fayl ID sini o'zi ajratadi.
3. `GET /api/v1/lessons/{id}/` (faqat yozilgan studentga) → `video_url` =
   `https://drive.google.com/file/d/<ID>/preview`, `video_type` = `iframe`. Frontend:
   `<iframe src="{video_url}" allow="autoplay" allowfullscreen>`.

Cheklovlar: havola ochiq — katalogda ko'rinmaydi, lekin texnik foydalanuvchi brauzerdan topishi mumkin;
ko'p ko'rilgan faylni Drive vaqtincha bloklashi mumkin ("quota exceeded").

**`bunny`** — Bunny Stream imzolangan HLS (`video_type` = `hls`, hls.js): `VIDEO_SIGNING_KEY`
(Pull Zone → Security → Token Authentication), `VIDEO_CDN_HOSTNAME`. Kalitlar bo'sh bo'lsa `video_url = null`.
O'tish uchun `.env` da `VIDEO_PROVIDER=bunny` va darslarga Bunny video ID larini yozish kifoya.

## Prod (VPS, Docker'siz)

Server: Ubuntu, kod `/srv/lesson24`, foydalanuvchi `lesson24`.

```bash
sudo adduser --system --group --home /srv/lesson24 lesson24
sudo -u lesson24 git clone <repo> /srv/lesson24 && cd /srv/lesson24
sudo -u lesson24 python3.12 -m venv venv
sudo -u lesson24 venv/bin/pip install -r requirements.txt
sudo -u lesson24 cp .env.example .env     # DEBUG=False, NUM_PROXIES=1, DATABASE_URL, ...

sudo cp deploy/*.service deploy/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lesson24-web lesson24-bot lesson24-cleanup.timer

sudo cp deploy/nginx.conf /etc/nginx/sites-available/lesson24
sudo ln -s /etc/nginx/sites-available/lesson24 /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d api.lesson24.uz

./deploy/deploy.sh     # migrate, createcachetable, collectstatic, check --deploy, restart
```

| Fayl | Vazifasi |
|---|---|
| `lesson24-web.service` | gunicorn, 3 worker, `127.0.0.1:8000` |
| `lesson24-bot.service` | Telegram bot (polling) |
| `lesson24-cleanup.timer` | har kuni 04:00 da `manage.py cleanup_codes` |
| `nginx.conf` | `/static/`, ommaviy `/media/`, yopiq `/media/payments/`, `internal` `/protected-media/` (X-Accel-Redirect) |
| `deploy.sh` | yangilash |

Muhim `.env` qiymatlari (prod):
- `DEBUG=False`, uzun tasodifiy `SECRET_KEY`
- `NUM_PROXIES=1` — Nginx ortida throttling haqiqiy IP bo'yicha ishlashi uchun
- Cloud baza: `DATABASE_URL=<provayder URL>`, `DB_SSLMODE=require`; pooler bo'lsa `DB_CONN_MAX_AGE=0`
- `media/` papkasi zaxira nusxalarga kiritilsin (to'lov cheklari shu yerda).
