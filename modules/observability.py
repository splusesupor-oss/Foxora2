"""
سیستم مانیتورینگ و مشاهده‌پذیری سلامت ربات (Observability & Health Monitoring)

جمع‌آوری کم‌هزینه و درون‌حافظه‌ای متریک‌ها برای ریشه‌یابی فشار و کندی پس از ساعت‌ها کارکرد:
- متریک‌های RPC (تعداد، دسته‌بندی، زمان پاسخ، خطاها، وضعیت Governor)
- زمان انتظار صف‌ها (Queue Wait Time در دیسپچر، صف حذف، صف مجازات، صف ارسال)
- وضعیت Workerها (تعداد فعال، پردازش‌شده، بازتولیدشده)
- آمار سرریزها (Overflow در دیسپچر و فرستنده)
- وضعیت Circuit Breaker به تفکیک گروه‌ها (Open / Half-Open / Closed)
- گزارش دوره‌ای سلامت (Periodic Health Snapshot / Heartbeat Log)
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional


def _get_rss_mb() -> float:
    try:
        with open("/proc/self/status", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("VmRSS"):
                    return float(line.split()[1]) / 1024.0
    except Exception:
        pass
    return 0.0


class MetricsCollector:
    """جمع‌آوری‌کنندهٔ مرکزی متریک‌های عملکردی ربات با حداقل سربار."""

    _instance: Optional["MetricsCollector"] = None

    def __init__(self, logger=None):
        self.logger = logger
        self.started_at = time.time()

        # ۱) متریک‌های RPC
        self.rpc_stats: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_ms": 0.0, "max_ms": 0.0, "errors": 0}
        )

        # ۲) زمان انتظار صف‌ها (نمونه‌برداری در پنجره‌های محدود)
        self.queue_waits: Dict[str, deque] = defaultdict(lambda: deque(maxlen=200))

        # ۳) سرریزها (Overflows)
        self.overflow_counts: Dict[str, int] = defaultdict(int)
        self.overflow_groups: Dict[str, int] = defaultdict(int)

        # ۴) خطاها و رخدادها
        self.recent_errors: deque = deque(maxlen=100)

    @classmethod
    def get_instance(cls, logger=None) -> "MetricsCollector":
        if cls._instance is None:
            cls._instance = cls(logger)
        elif logger and not cls._instance.logger:
            cls._instance.logger = logger
        return cls._instance

    def record_rpc(self, category: str, elapsed_ms: float, is_error: bool = False):
        """ثبت یک درخواست RPC و زمان پاسخ آن."""
        stat = self.rpc_stats[category or "unknown"]
        stat["count"] += 1
        stat["total_ms"] += float(elapsed_ms)
        if elapsed_ms > stat["max_ms"]:
            stat["max_ms"] = float(elapsed_ms)
        if is_error:
            stat["errors"] += 1

    def record_queue_wait(self, queue_name: str, wait_ms: float):
        """ثبت زمان انتظار یک تسک در صف."""
        self.queue_waits[queue_name].append(float(wait_ms))

    def record_overflow(self, source: str, chat_id: Any = None):
        """ثبت سرریز در یک صف مشخص."""
        self.overflow_counts[source] += 1
        if chat_id is not None:
            self.overflow_groups[str(chat_id)] += 1
            while len(self.overflow_groups) > 200:
                self.overflow_groups.pop(next(iter(self.overflow_groups)), None)

    def record_error(self, component: str, error_message: str):
        """ثبت خلاصه خطا برای گزارش تشخیصی."""
        self.recent_errors.append({
            "time": time.time(),
            "component": component,
            "error": str(error_message)[:200],
        })

    def get_queue_stats(self) -> Dict[str, Dict[str, float]]:
        """محاسبه میانگین و حداکثر زمان انتظار در صف‌ها."""
        res = {}
        for q_name, samples in self.queue_waits.items():
            if samples:
                res[q_name] = {
                    "avg_ms": sum(samples) / len(samples),
                    "max_ms": max(samples),
                    "samples": len(samples),
                }
            else:
                res[q_name] = {"avg_ms": 0.0, "max_ms": 0.0, "samples": 0}
        return res

    def get_rpc_summary(self) -> Dict[str, Any]:
        """گزارش تفکیکی عملکرد RPC."""
        summary = {}
        for cat, data in self.rpc_stats.items():
            cnt = data["count"]
            avg_ms = (data["total_ms"] / cnt) if cnt > 0 else 0.0
            summary[cat] = {
                "count": cnt,
                "avg_ms": round(avg_ms, 2),
                "max_ms": round(data["max_ms"], 2),
                "errors": data["errors"],
            }
        return summary

    def get_system_snapshot(self, bot: Any = None) -> Dict[str, Any]:
        """تهیهٔ تصویر کامل از وضعیت تمام بخش‌های ربات."""
        now = time.time()
        uptime_sec = max(0, int(now - self.started_at))

        # ۱) حافظه و پردازنده
        rss_mb = _get_rss_mb()

        # ۲) وضعیت صف‌ها و ورکرها
        workers_stat = {}
        dispatcher_stats = {}
        if bot:
            disp = getattr(bot, "group_dispatcher", None)
            if disp:
                workers_stat["dispatcher"] = disp.worker_count()
                dispatcher_stats = dict(getattr(disp, "stats", {}) or {})

            del_q = getattr(bot, "message_delete_queue", None)
            if del_q:
                active_del_workers = sum(1 for w in getattr(del_q, "_workers", {}).values() if w and not w.done())
                pending_del = sum(q.qsize() for q in getattr(del_q, "_queues", {}).values())
                workers_stat["delete_queue"] = {
                    "workers": active_del_workers,
                    "pending_chats": len(getattr(del_q, "_queues", {})),
                    "pending_items": pending_del,
                }

            mod_q = getattr(bot, "moderation_queue", None)
            if mod_q:
                active_mod_workers = sum(
                    len([w for w in lst if not w.done()])
                    for lst in getattr(mod_q, "_workers", {}).values()
                )
                workers_stat["moderation_queue"] = {
                    "workers": active_mod_workers,
                    "pending_keys": len(getattr(mod_q, "_pending_keys", set())),
                }

            sender = getattr(bot, "outgoing_sender", None)
            if sender:
                active_send_workers = sum(
                    len([w for w in lst if not w.done()])
                    for lst in getattr(sender, "_workers", {}).values()
                )
                workers_stat["outgoing_sender"] = {
                    "workers": active_send_workers,
                    "stats": dict(getattr(sender, "stats", {}) or {}),
                }

        # ۳) وضعیت مدارشکن (Circuit Breaker)
        cb_stats = {"total_tracked": 0, "open": 0, "half_open": 0, "closed": 0, "open_groups": []}
        try:
            from modules.cache_manager import PermissionCircuitBreaker, STATE_OPEN, STATE_HALF_OPEN, STATE_CLOSED
            cb = PermissionCircuitBreaker.get_default()
            if cb:
                snap = cb.snapshot()
                breakers = getattr(cb, "_breakers", {})
                cb_stats["total_tracked"] = len(breakers)
                for chat_key, rec in breakers.items():
                    if rec.state == STATE_OPEN:
                        cb_stats["open"] += 1
                        cb_stats["open_groups"].append(chat_key)
                    elif rec.state == STATE_HALF_OPEN:
                        cb_stats["half_open"] += 1
                    else:
                        cb_stats["closed"] += 1
        except Exception:
            pass

        # ۴) وضعیت Governor
        governor_info = {}
        if bot:
            gov = getattr(bot, "rpc_governor", None)
            if gov:
                try:
                    governor_info = gov.snapshot()
                except Exception:
                    pass

        return {
            "uptime_seconds": uptime_sec,
            "rss_mb": round(rss_mb, 1),
            "rpc_summary": self.get_rpc_summary(),
            "queue_waits": self.get_queue_stats(),
            "overflows": {
                "counts": dict(self.overflow_counts),
                "top_groups": sorted(self.overflow_groups.items(), key=lambda x: x[1], reverse=True)[:5],
            },
            "circuit_breaker": cb_stats,
            "governor": governor_info,
            "workers": workers_stat,
            "dispatcher_stats": dispatcher_stats,
        }

    def format_diagnostic_report(self, bot: Any = None) -> str:
        """قالب‌بندی گزارش تشخیصی خوانا برای مالک و لاگ‌ها."""
        snap = self.get_system_snapshot(bot)
        up_s = snap["uptime_seconds"]
        up_h, rem = divmod(up_s, 3600)
        up_m = rem // 60

        lines = [
            "📊 گزارش جامع سلامت و عملکرد ربات (Observability)",
            "",
            f"⏱ مدت اجرا: {up_h} ساعت و {up_m} دقیقه",
            f"🧠 حافظه مصرفی (RSS): {snap['rss_mb']} MB",
            "",
            "🚦 وضعیت بودجه RPC (Governor):",
        ]

        gov = snap.get("governor", {})
        if gov:
            mode = "Shadow" if gov.get("shadow") else ("فعال" if gov.get("enabled") else "خاموش")
            lines.append(f"  • وضعیت: {mode} | فعال: {gov.get('active', 0)}/{gov.get('total_limit', 0)} | در صف انتظار: {gov.get('waiting', 0)}")
        else:
            lines.append("  • در دسترس نیست")

        lines.extend([
            "",
            "⚡ زمان انتظار صف‌ها (Queue Wait Time):",
        ])
        q_waits = snap.get("queue_waits", {})
        if q_waits:
            for q_name, stats in q_waits.items():
                lines.append(f"  • {q_name}: میانگین={stats['avg_ms']:.1f}ms | بیشینه={stats['max_ms']:.1f}ms")
        else:
            lines.append("  • بدون تاخیر ثبت‌شده")

        lines.extend([
            "",
            "🔌 وضعیت مدارشکن‌ها (Circuit Breaker):",
        ])
        cb = snap.get("circuit_breaker", {})
        lines.append(f"  • کل گروه‌های ثبتی: {cb.get('total_tracked', 0)} | سالم (Closed): {cb.get('closed', 0)} | قطع (Open): {cb.get('open', 0)}")
        if cb.get("open_groups"):
            lines.append(f"  • گروه‌های مسدود به دلیل عدم دسترسی: {', '.join(str(g) for g in cb['open_groups'][:5])}")

        lines.extend([
            "",
            "🌊 آمار سرریزها (Overflows):",
        ])
        of = snap.get("overflows", {})
        counts = of.get("counts", {})
        if counts:
            for src, cnt in counts.items():
                lines.append(f"  • {src}: {cnt} مورد")
        else:
            lines.append("  • ۰ سرریز (تمام پیام‌ها پردازش شده‌اند)")

        return "\n".join(lines)


class PeriodicHealthMonitor:
    """اجراکنندهٔ دوره‌ای بررسی سلامت و ثبت Heartbeat در لاگ."""

    def __init__(self, bot: Any, logger: Any, interval_seconds: float = 600.0):
        self.bot = bot
        self.logger = logger
        self.interval_seconds = max(0.01, float(interval_seconds))
        self._task: Optional[asyncio.Task] = None
        self._closed = False
        self.collector = MetricsCollector.get_instance(logger)

    def start(self):
        if self._task is None or self._task.done():
            self._closed = False
            self._task = asyncio.create_task(
                self._run_loop(), name="bot-periodic-health-monitor"
            )

    async def _run_loop(self):
        while not self._closed:
            try:
                await asyncio.sleep(self.interval_seconds)
                report = self.collector.format_diagnostic_report(self.bot)
                if self.logger:
                    self.logger.log_info(
                        f"HEALTH_HEARTBEAT_REPORT\n{report}"
                    )
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self.logger:
                    self.logger.log_error(f"HEALTH MONITOR LOOP ERROR: {e!r}")

    async def stop(self):
        self._closed = True
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
