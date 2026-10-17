# Foxora — قابلیت‌های حفظ‌شده از نسخهٔ قبلی (لاگ/CPU/صف) روی پایهٔ واقعی B2

این سند دقیق می‌گوید کدام قابلیت‌های پروژهٔ قبلی Foxora قبل از جایگزینی با کد
واقعی B2 شناسایی و حفظ شدند، الان در کدام فایل‌ها زندگی می‌کنند و روی چه
مسیر اجرایی واقعاً فعال‌اند.

## الف) بهینه‌سازی لاگ و CPU

| گارانتی Foxora قبلی | وضعیت در نسخهٔ جدید | فایل/تست |
|---|---|---|
| لاگ‌نویسی نباید رویداد اصلی را block کند | Queue-based logging با صف bounded (10k) و handler «drop-on-saturation» که هرگز caller را block نمی‌کند | `modules/logger_module.py` (`_DroppingQueueHandler`) — تست: `tests/test_foxora_logging_runtime.py::test_dropping_handler_never_blocks_the_caller` |
| سرکوب لاگ‌های تکراری/پرحجم هر پیام | فهرست `_NOISY_PREFIXES`؛ فقط با `BOT_VERBOSE_LOGS=1` بازیابی می‌شود | `modules/logger_module.py` — تست‌های `test_noisy_prefixes_are_suppressed_in_production` و `test_verbose_mode_restores_noisy_lines` |
| چرخش لاگ و سقف حجم فایل‌ها | `RotatingFileHandler` برای `bot.log`، `deleted_messages.log`، `actions.log` با `BOT_LOG_MAX_BYTES` (پیش‌فرض 20MB) و `BOT_LOG_BACKUPS` (پیش‌فرض 5) | `modules/logger_module.py` — تست end-to-end چرخش: `test_log_rotation_stays_bounded_inside_foxora_home` |
| audit پیام‌های حذف‌شده بدون block شدن worker ورودی | listener دوم با صف bounded جدا + برش متن پیام به ۵۰۰ کاراکتر | `modules/logger_module.py` (`log_deleted_message`) |
| جلوگیری از رشد بی‌رویهٔ state | نگاشت‌های کران‌دار در `modules/cache_manager.py`, `modules/runtime_snapshot.py`, `modules/runtime_maintenance.py`, پیگیری‌کننده‌های bounded (`message_tracker`, `spam_history` و…) — همان پیاده‌سازی زندهٔ B2 |

## ب) صف درخواست‌های گروه‌ها

| گارانتی Foxora قبلی | وضعیت در نسخهٔ جدید | فایل/تست |
|---|---|---|
| صف RPC خروجی: یک‌در‌لحظه، منصفانه بین گروه‌ها، bounded | پورت مستقیم «SerialRpcQueue» + knobهای env (total 1-4096، per-group 1-256) | `modules/rpc_queue.py` — تست: `tests/test_foxora_rpc_queue.py` |
| pacing بین فراخوان‌های RPC (`RPC_MIN_INTERVAL_SECONDS`, بازه 0-60) | هم در `SerialRpcQueue` و هم روی **مسیر زندهٔ تولید** یعنی `RpcGovernor.acquire` (پیش‌فرض 0 = رفتار دقیق B2؛ مقدار `.env` فاصله‌گذاری را فعال می‌کند) | `modules/rpc_governor.py` (`_pace_start`) — تست: `tests/test_foxora_governor_pacing.py` |
| صف moderation منصفانهٔ هرگروه با سقف کل/گروه/تعداد گروه‌ها | پورت مستقیم «FairModerationQueue» | `modules/fair_queue.py` — تست: `tests/test_foxora_fair_queue.py` |
| صف delete با dedupe فقط روی همان (گروه، پیام) و کران ظرفیت | پورت مستقیم «FairDeleteQueue» | `modules/fair_delete_queue.py` — تست: `tests/test_foxora_delete_queue_capacity.py` |
| عدم ادغام درخواست‌ها | هر job یک عمل شبکهٔ مستقل می‌ماند؛ dedupe فقط با `idempotency_key` صریح یا همان جفت (گروه، پیام) تکراری در انتظار | در اسناد `modules/rpc_queue.py` و `modules/fair_delete_queue.py` |
| محدودیت نرخ، ترتیب اجرا، ضد-ازدحام، مدیریت خطا | زنجیرهٔ زندهٔ B2 حفظ شده: `modules/group_dispatch.py` → `modules/moderation_queue.py` (سقف ۳ کاربر هم‌زمان/گروه، FloodWait retry) و `modules/outgoing_sender.py` (صف هرچت + اولویت) → `modules/rpc_governor.py` (سقف in-flight + کلاس‌های اولویت + fairness هرچت) | تست‌های B2: `tests/test_rpc_governor.py`, `tests/test_low_latency_queues.py`, `tests/test_step3_multigroup_spam_and_fairness.py` |

> نکتهٔ پروتکلی: هیچ‌کدام از صف‌ها ادعای «ترکیب چند درخواست به یک درخواست
> شبکه» نمی‌کنند و نمی‌توانند — سرور سروش پلاس هر فراخوان را جدا می‌بیند.
> صف‌ها فقط زمان شروع، ترتیب و نرخ را کنترل می‌کنند.

## ج) جداسازی مسیر اجرا

| مورد | فایل/تست |
|---|---|
| مسیر مستقل `~/.foxora` برای instance «foxora» | `modules/runtime_paths.py` — تست: `tests/test_foxora_path_isolation.py` |
| نشانگر `.bot_instance` = `foxora` | ریشهٔ مخزن — تست marker در همان فایل تست |
| session فقط در حافظه + env اختصاصی `SOROUSH_SESSION_STRING_FOXORA` | `core/bot_working_split_ok.py` |
| قفل PID per-instance: `~/.foxora/config/bot.pid` | `main.py`, `watchdog.py` |
