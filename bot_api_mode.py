"""
حالت جایگزین: استفاده از API رسمی ربات سروش پلاس (bot.splus.ir)
این حالت زمانی استفاده می‌شود که شما یک ربات رسمی ساخته‌اید (از طریق @mrbot)
و نه یوزر-بات.

محدودیت‌ها:
- ربات‌های رسمی در سروش پلاس معمولا دسترسی حذف پیام یا بن کردن ندارند مگر اینکه ادمین کانال/گروه باشند و API اجازه دهد
- این کد برای مانیتورینگ و ارسال هشدار استفاده می‌شود

برای استفاده:
1. به @MrBot در سروش پلاس بروید و ربات بسازید
2. توکن را در config.json یا .env بگذارید (BOT_TOKEN)
3. این فایل را اجرا کنید: python bot_api_mode.py
"""

import os
import json
import time
import requests
from dotenv import load_dotenv
import sys
sys.path.insert(0, os.path.dirname(__file__))

from modules import ConfigManager, SpamDetector, BotLogger, UserTracker

load_dotenv()

class SoroushOfficialBot:
    def __init__(self, token: str, config_path="config/config.json"):
        self.token = token
        self.base_url = f"https://bot.splus.ir/v2/{token}"
        self.base_url_v1 = f"https://bot.splus.ir/{token}"
        
        self.config_manager = ConfigManager(config_path=config_path)
        self.logger = BotLogger()
        self.detector = SpamDetector(self.config_manager)
        self.tracker = UserTracker(
            spam_counts_file=self.config_manager.get("spam_counts_file", "logs/spam_counts.json"),
            threshold=self.config_manager.get("spam_threshold", 3)
        )
        self.headers = {'Content-Type': 'Application/json', 'Accept': 'Application/json'}

    def get_messages_sse(self):
        """دریافت پیام‌ها با SSE (روش رسمی)"""
        try:
            import sseclient
            url = f"{self.base_url}/getMessage"
            response = requests.get(url, stream=True, headers=self.headers, timeout=90)
            client = sseclient.SSEClient(response)
            for event in client.events():
                try:
                    message = json.loads(event.data)
                    yield message
                except Exception as e:
                    print(f"خطا در پارس پیام SSE: {e}")
                    continue
        except ImportError:
            print("❌ کتابخانه sseclient-py نصب نیست. pip install sseclient-py")
            # روش fallback polling
            yield from self.get_messages_polling()
        except Exception as e:
            print(f"خطا در اتصال SSE: {e}. تلاش دوباره...")
            time.sleep(5)
            yield from self.get_messages_sse()

    def get_messages_polling(self):
        """روش پولینگ ساده (اگر SSE کار نکرد)"""
        print("🔄 استفاده از حالت Polling...")
        while True:
            try:
                # سروش API رسمی getMessage با GET
                url = f"{self.base_url}/getMessage"
                resp = requests.get(url, headers=self.headers, stream=True, timeout=30)
                # این API معمولا استریم است، ولی برای تست
                print("پاسخ خام:", resp.text[:500])
                time.sleep(2)
            except Exception as e:
                print(f"Polling error: {e}")
                time.sleep(5)

    def send_message(self, to: str, text: str, reply_to: str = None):
        """ارسال پیام"""
        try:
            data = {
                "type": "TEXT",
                "to": to,
                "body": text
            }
            # اگر ریپلای
            if reply_to:
                data["replyTo"] = reply_to

            jdata = json.dumps(data, separators=(',', ':'))
            resp = requests.post(f"{self.base_url_v1}/sendMessage", data=jdata, headers=self.headers, timeout=10)
            print(f"ارسال به {to}: {resp.status_code} {resp.text[:200]}")
            return resp.ok
        except Exception as e:
            print(f"خطا در ارسال: {e}")
            return False

    def delete_message_api(self, chat_id: str, message_id: str):
        """
        تلاش برای حذف - در API رسمی سروش ممکن است endpoint متفاوتی باشد
        مستندات دقیق: https://bot.splus.ir/docs
        فعلا به صورت لاگ و تلاش
        """
        try:
            # برخی نسخه‌های API حذف را ساپورت می‌کنند
            data = {
                "type": "DELETE_MESSAGE",
                "to": chat_id,
                "messageId": message_id
            }
            resp = requests.post(f"{self.base_url_v1}/deleteMessage", data=json.dumps(data), headers=self.headers)
            print(f"تلاش حذف: {resp.text}")
            return resp.ok
        except Exception as e:
            print(f"حذف API پشتیبانی نمی‌شود یا خطا: {e}")
            return False

    def process_message(self, msg: dict):
        """پردازش یک پیام دریافتی"""
        try:
            # ساختار پیام سروش:
            # {
            #   "from": "chat_id",
            #   "to": "...",
            #   "type": "TEXT",
            #   "body": "متن",
            #   "fromUsername": "...",
            #   ...
            # }
            from_id = msg.get("from", "")
            body = msg.get("body", "") or msg.get("text", "")
            msg_type = msg.get("type", "TEXT")
            message_id = msg.get("id") or msg.get("messageId")
            from_username = msg.get("fromUsername") or msg.get("sender", "")

            if not body or msg_type != "TEXT":
                return

            print(f"📩 پیام جدید از {from_id} ({from_username}): {body[:80]}")

            # بررسی وایت لیست - نیاز به نگاشت user_id واقعی
            # در API ربات، from معمولا chat_id است و نه user_id عددی
            # برای سادگی، وایت لیست را با username چک می‌کنیم
            if from_username and from_username.lstrip('@').lower() in self.config_manager.whitelisted_usernames:
                return

            is_spam, reason = self.detector.is_spam(body)
            if is_spam:
                print(f"🚫 اسپم تشخیص داده شد: {reason}")
                count = self.tracker.increment(from_id, from_id)  # در اینجا from_id را به عنوان کلید استفاده می‌کنیم

                self.logger.log_deleted_message(
                    user_id=hash(from_id) % 1000000,  # چون id عددی نداریم
                    username=from_username,
                    group_id=hash(from_id) % 1000000,
                    group_title=from_id,
                    original_text=body,
                    reason=reason,
                    message_id=message_id
                )

                # تلاش برای حذف (اگر API اجازه دهد)
                if self.config_manager.get("delete_spam"):
                    self.delete_message_api(from_id, message_id)

                # ارسال هشدار
                warning = self.config_manager.get("warning_message", "⚠️ پیام شما به دلیل {reason} حذف شد").format(
                    username=from_username or "کاربر",
                    reason=reason,
                    count=count,
                    threshold=self.config_manager.get("spam_threshold", 3)
                )
                self.send_message(from_id, warning)

                if self.tracker.should_punish(from_id, from_id):
                    self.send_message(from_id, f"⛔️ کاربر {from_username} به دلیل اسپم مکرر گزارش شد.")
            else:
                # دستورات ادمین
                if body.strip().startswith("!stats"):
                    stats = self.tracker.get_all_counts()
                    self.send_message(from_id, f"📊 آمار: {json.dumps(stats, ensure_ascii=False)[:500]}")

        except Exception as e:
            print(f"خطا در پردازش پیام: {e}")
            import traceback
            traceback.print_exc()

    def run(self):
        print("="*50)
        print("🤖 ربات سروش پلاس (حالت API رسمی) روشن شد")
        print(f"🔑 توکن: {self.token[:10]}...")
        print("="*50)
        for msg in self.get_messages_sse():
            self.process_message(msg)


if __name__ == "__main__":
    token = os.getenv("BOT_TOKEN") or os.getenv("SOROUSH_BOT_TOKEN")
    if not token:
        # از config بخوان
        try:
            with open("config/config.json", 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                token = cfg.get("bot_token", "")
        except:
            pass

    if not token:
        print("❌ توکن ربات یافت نشد!")
        print("لطفا توکن را در فایل .env به نام BOT_TOKEN یا در config/config.json قرار دهید")
        print("برای ساخت ربات به @MrBot در سروش پلاس مراجعه کنید")
        exit(1)

    bot = SoroushOfficialBot(token=token)
    bot.run()
