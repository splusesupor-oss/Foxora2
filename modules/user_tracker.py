"""
ردیاب تعداد هرزنامه‌های هر کاربر + مدیریت وضعیت mute/ban
"""
import os
import json
import threading
from datetime import datetime, timedelta
from typing import Dict
from collections import defaultdict
from pathlib import Path

from modules.group_id import normalize_group_id
from modules.runtime_paths import runtime_log_file
from modules.atomic_write import write_json


class UserTracker:
    def __init__(self, spam_counts_file: str = "logs/spam_counts.json", threshold: int = 3):
        self.spam_counts_file = str(
            runtime_log_file(Path(spam_counts_file).name, migrate=True)
            if spam_counts_file == "logs/spam_counts.json"
            else spam_counts_file
        )
        self.threshold = threshold
        self._lock = threading.RLock()
        self._generation = 0
        self.spam_counts: Dict[str, Dict[str, int]] = {}  # {group_id: {user_id: count}}
        self.muted_users: Dict[str, datetime] = {}  # برای پیگیری زمان mute
        self.banned_users: Dict[str, datetime] = {}  # برای پیگیری وضعیت ban
        self._dirty = False
        os.makedirs(os.path.dirname(spam_counts_file) if os.path.dirname(spam_counts_file) else ".", exist_ok=True)
        self.load()

    def load(self):
        if os.path.exists(self.spam_counts_file):
            try:
                with open(self.spam_counts_file, 'r', encoding='utf-8') as f:
                    self.spam_counts = json.load(f)
            except:
                self.spam_counts = {}
        else:
            self.spam_counts = {}

    def mark_dirty(self):
        with self._lock:
            self._dirty = True
            self._generation += 1

    def save(self, force=False):
        """Atomically persist a stable snapshot without racing hot updates."""
        with self._lock:
            if not force and not self._dirty:
                return False
            generation = self._generation
            snapshot = {
                str(group): dict(users)
                for group, users in self.spam_counts.items()
            }
        write_json(self.spam_counts_file, snapshot)
        with self._lock:
            if self._generation == generation:
                self._dirty = False
        return True

    def _key(self, group_id, user_id):
        return normalize_group_id(group_id), str(user_id)


    def set_muted(self, group_id: int, user_id: int, until: datetime):
        """ثبت کاربر mute شده"""
        key = f"{normalize_group_id(group_id)}:{user_id}"
        self.muted_users[key] = until

    def is_muted(self, group_id: int, user_id: int) -> bool:
        """بررسی وضعیت mute"""
        key = f"{normalize_group_id(group_id)}:{user_id}"
        if key not in self.muted_users:
            return False
        if datetime.now() >= self.muted_users[key]:
            del self.muted_users[key]
            return False
        return True

    def set_banned(self, group_id: int, user_id: int):
        """ثبت کاربر ban شده"""
        key = f"{normalize_group_id(group_id)}:{user_id}"
        self.banned_users[key] = datetime.now()

    def is_banned(self, group_id: int, user_id: int) -> bool:
        """بررسی وضعیت ban"""
        return f"{normalize_group_id(group_id)}:{user_id}" in self.banned_users

    def increment(self, group_id: int, user_id: int) -> int:
        """افزایش شمارنده هرزنامه و برگرداندن تعداد جدید"""
        g_key, u_key = self._key(group_id, user_id)
        with self._lock:
            group = self.spam_counts.setdefault(g_key, {})
            group[u_key] = int(group.get(u_key, 0)) + 1
            self.mark_dirty()
            return group[u_key]

    def get_count(self, group_id: int, user_id: int) -> int:
        g_key, u_key = self._key(group_id, user_id)
        with self._lock:
            return self.spam_counts.get(g_key, {}).get(u_key, 0)

    def should_punish(self, group_id: int, user_id: int, threshold: int = None) -> bool:
        """آیا کاربر باید مجازات شود (بیش از آستانه)

        ``threshold`` اختیاری است: اگر داده شود (مثلاً آستانهٔ per-group از
        دستور «تغییر اخطار»)، به‌جای آستانهٔ سراسری استفاده می‌شود.
        """
        limit = self.threshold if threshold is None else int(threshold)
        return self.get_count(group_id, user_id) >= limit

    def reset_count(self, group_id: int, user_id: int):
        g_key, u_key = self._key(group_id, user_id)
        with self._lock:
            if g_key in self.spam_counts and u_key in self.spam_counts[g_key]:
                del self.spam_counts[g_key][u_key]
                if not self.spam_counts[g_key]:
                    self.spam_counts.pop(g_key, None)
                self.mark_dirty()

    def decrement(self, group_id: int, user_id: int) -> int:
        """یک اخطار/تخلف را امن کم می‌کند (کم‌تر از صفر نمی‌رود).

        برمی‌گرداند تعدادِ جدیدِ تخلفات.
        """
        g_key, u_key = self._key(group_id, user_id)
        with self._lock:
            current = self.spam_counts.get(g_key, {}).get(u_key, 0)
            new_count = max(current - 1, 0)
            if new_count <= 0:
                # صفر شد → رکورد را به‌کلی حذف می‌کنیم تا با بقیهٔ اطلاعات تداخل نکند.
                if g_key in self.spam_counts and u_key in self.spam_counts[g_key]:
                    del self.spam_counts[g_key][u_key]
                    if not self.spam_counts[g_key]:
                        self.spam_counts.pop(g_key, None)
                    self.mark_dirty()
                return 0
            self.spam_counts.setdefault(g_key, {})[u_key] = new_count
            self.mark_dirty()
            return new_count

    def reset_group(self, group_id: int):
        g_key = normalize_group_id(group_id)
        with self._lock:
            if g_key in self.spam_counts:
                del self.spam_counts[g_key]
                self.mark_dirty()

    def get_all_counts(self, group_id: int = None):
        with self._lock:
            if group_id is None:
                return {group: dict(users)
                        for group, users in self.spam_counts.items()}
            return dict(self.spam_counts.get(normalize_group_id(group_id), {}))

    def get_top_spammers(self, group_id: int = None, limit: int = 10):
        """لیست بیشترین اسپمرها"""
        with self._lock:
            if group_id:
                group_data = self.spam_counts.get(normalize_group_id(group_id), {})
                sorted_users = sorted(group_data.items(), key=lambda x: x[1], reverse=True)
                return sorted_users[:limit]
            # در کل گروه‌ها
            total = defaultdict(int)
            for users in self.spam_counts.values():
                for user_id, count in users.items():
                    total[user_id] += count
            return sorted(total.items(), key=lambda x: x[1], reverse=True)[:limit]
