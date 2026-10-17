"""پروفایل سبک مسیر پیام؛ فقط پیام‌های کند را لاگ می‌کند."""
import os
import time

_PERF_DEBUG = os.getenv("BOT_PERF_DEBUG", "").strip() == "1"


class MessagePerformance:
    STAGES = (
        "RECEIVE",
        "ADMIN_CHECK",
        "FILTER",
        "SPAM_CHECK",
        "FORWARD_CHECK",
        "COMMAND_MATCH",
        "AUTO_MODERATION",
        "SEND_RESPONSE",
    )

    def __init__(self):
        self.started_at = time.perf_counter()
        self._last_at = self.started_at
        self.values = {stage: 0.0 for stage in self.STAGES}

    def mark(self, stage):
        now = time.perf_counter()
        if stage in self.values:
            self.values[stage] += (now - self._last_at) * 1000
        self._last_at = now

    def skip_to(self):
        """Drop time since the last mark so the next stage is not a junk bucket."""
        self._last_at = time.perf_counter()

    def set(self, stage, value_ms):
        if stage in self.values:
            self.values[stage] = max(0.0, float(value_ms))

    def finish(self, logger, chat_id, threshold_ms=1000):
        now = time.perf_counter()
        total_ms = (now - self.started_at) * 1000
        slowest_stage = max(
            self.STAGES, key=lambda stage: self.values.get(stage, 0.0)
        )
        result = {
            "total_ms": total_ms,
            "slowest_stage": slowest_stage,
            "slowest_ms": self.values.get(slowest_stage, 0.0),
            "stages": dict(self.values),
        }
        if total_ms < threshold_ms:
            return result
        parts = []
        for stage in self.STAGES:
            value = self.values[stage]
            parts.append(f"{stage.lower()}={value:.2f}ms")
            if value > threshold_ms:
                logger.log_error(
                    f"PERF WARNING chat_id={chat_id} stage={stage} elapsed_ms={value:.2f}"
                )
        if _PERF_DEBUG:
            logger.log_info(
                "PERF " + " ".join(parts) + f" TOTAL={total_ms:.2f}ms"
            )
        return result
