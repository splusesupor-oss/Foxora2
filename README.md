# 🦊 Foxora2 — ربات مدیریت گروه و ضد اسپم سروش پلاس (نسخهٔ دوم کاملاً مستقل)

**Foxora2** یک کپی کامل و کاملاً مستقل از ربات Foxora برای سروش پلاس است. تمام قابلیت‌ها، دستورات، بازی‌ها، اقتصاد، مدیریت گروه، قالب‌بندی پیام‌ها (Bold / Quote / Spoiler) و فهرست‌های داده بدون هیچ تغییری حفظ شده‌اند، اما **مسیر نصب، مسیر داده‌ها، دیتابیس، لاگ‌ها، فایل قفل پروسه (PID / Watchdog) و نشست (Session)** کاملاً از ربات `Foxora` و ربات `B2` جدا هستند.

---

## 🔒 جداسازی کامل از ربات‌های دیگر (`Foxora` و `B2`)

| مورد | ربات `Foxora2` (این مخزن) | ربات `Foxora` (اصلی) | ربات `B2` |
|---|---|---|---|
| پوشهٔ کد پروژه | `~/Foxora2` | `~/Foxora` | پوشهٔ جداگانهٔ B2 |
| شناسهٔ نمونه (`.bot_instance` / `BOT_INSTANCE`) | `foxora2` | `foxora` | `main` |
| مسیر داده‌ها، دیتابیس و لاگ | `~/.foxora2/` | `~/.foxora/` | `~/.local/share/soroush-bot/` |
| دیتابیس SQLite | `~/.foxora2/db/bot.sqlite3` | `~/.foxora2` ندارد (`~/.foxora/db/bot.sqlite3`) | `~/.local/share/soroush-bot/db/bot.sqlite3` |
| فایل قفل پروسه و واچ‌داگ | `~/.foxora2/config/bot.pid` و `watchdog.lock` | `~/.foxora/config/bot.pid` و `watchdog.lock` | مسیر مستقل B2 |
| متغیر نشست در `.env` | `SOROUSH_SESSION_STRING_FOXORA2` | `SOROUSH_SESSION_STRING_FOXORA` | `SOROUSH_SESSION_STRING` |
| دستور اجرا / توقف | `./foxora2` و `python3 foxora2.py` | `./foxora` و `python3 foxora.py` | مستقل |

> ⚠️ **مهم:** یک حساب سروش (یک `SOROUSH_SESSION_STRING`) نمی‌تواند همزمان روی دو پروسهٔ فعال اجرا شود؛ سرور MTProto آپدیت‌ها را فقط به یکی می‌دهد. برای اجرای همزمان `Foxora2` در کنار `Foxora` و `B2`، حتماً یک حساب سروش جداگانه برای `Foxora2` بسازید و نشست آن را در `SOROUSH_SESSION_STRING_FOXORA2` بگذارید.

---

## 📦 نصب و راه‌اندازی در Termux یا لینوکس

### ۱. کلون کردن در پوشهٔ مستقل
```bash
git clone https://github.com/splusesupor-oss/Foxora2.git ~/Foxora2
cd ~/Foxora2
```

### ۲. نصب پیش‌نیازها
```bash
pip install -r requirements.txt
```

### ۳. ساخت فایل `.env` و تنظیم نشست اختصاصی `Foxora2`
```bash
cp .env.example .env
python3 tools/login_primary_once.py
```
ابزار `tools/login_primary_once.py` شماره تلفن و کد تأیید حساب جدید سروش را یک‌بار می‌پرسد و رشتهٔ نشست را مستقیماً در متغیر اختصاصی `SOROUSH_SESSION_STRING_FOXORA2` داخل `.env` ذخیره می‌کند (بدون دست‌زدن به نشست `Foxora` یا `B2`).

همچنین می‌توانید فایل `.env` را دستی ویرایش کنید (`nano .env`):
```env
BOT_INSTANCE=foxora2
SOROUSH_SESSION_STRING_FOXORA2=رشته_نشست_حساب_جدید_سروش
```

---

## ▶️ اجرای ربات `Foxora2`

| دستور | عملکرد |
|---|---|
| `./foxora2` | اجرای ربات با واچ‌داگ اختصاصی (پیشنهادی برای اجرای دائمی) |
| `./foxora2 direct` یا `python3 foxora2.py` | اجرای مستقیم بدون واچ‌داگ |
| `./foxora2 status` | نمایش وضعیت فعلی، PID و مسیر داده‌های `Foxora2` |
| `./foxora2 stop` | توقف **فقط** ربات `Foxora2` (ربات‌های `Foxora` و `B2` دست‌نخورده می‌مانند) |

اگر `./foxora2` خطای دسترسی داد، یک‌بار دستور زیر را بزنید:
```bash
chmod +x foxora2 foxora2.py
```

---

## 🗂️ مسیر ذخیرهٔ اطلاعات `Foxora2`

تمام فایل‌های زمان اجرا به صورت خودکار در `~/.foxora2` ساخته می‌شوند:
- `~/.foxora2/config/` — گروه‌های فعال (`groups.json`)، ادمین‌ها (`admins.json`)، قوانین، کلمات، اقتصاد، کلیپ‌بورد، قفل پروسه (`bot.pid` و `watchdog.lock`) و تنظیمات
- `~/.foxora2/db/bot.sqlite3` — دیتابیس SQLite اختصاصی `Foxora2`
- `~/.foxora2/logs/` — لاگ‌های زمان اجرا (`bot_runtime.log`، `watchdog.log`)، پیام‌های حذف‌شده و آمار گروه‌ها
- `~/.foxora2/backups/` و `~/.foxora2/archive/` — پشتیبان‌ها و آرشیوها

در صورت نیاز به تغییر مسیر داده‌ها می‌توانید متغیر زیر را در `.env` قرار دهید:
```env
SOROUSH_BOT_DATA_DIR_FOXORA2=/custom/path/for/foxora2
```
