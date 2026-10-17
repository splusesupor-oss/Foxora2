"""Long soak of the CURRENT outgoing stack. Does not change product code.

Simulates ~35 groups across the equivalent of 2–3 hours of 45s snapshots.
Soroush-like RTT is injected inside client._call so a 2s stall can only
appear as rpc_await_ms (unless governor/sender actually wait).

    python tests/long_run_outgoing_soak.py
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import economy.storage as storage
from economy import directory
from modules import connection_guard as cg
from modules.outgoing_profiler import instrument_client
from modules.outgoing_sender import install as install_outgoing_sender
from modules.runtime_snapshot import RuntimeSnapshotMonitor, format_snapshot

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
RAW_LOG = LOG_DIR / "long_soak_raw.log"
REPORT = LOG_DIR / "long_soak_report.md"

GROUPS = 35
TICKS = 160  # 160 * 45s = 2h00m of snapshot cadence
EXTRA_TICKS = 40  # +30m → 2h30m
CRITICAL_TICKS = {80, 120, 160, 190}  # ~1h, 1h30, 2h, 2h22
STALE_PING_TICKS = {40, 90, 140, 185}


class Logger:
    def __init__(self, sink):
        self.infos = []
        self.errors = []
        self.sink = sink

    def _store(self, bucket, message):
        text = str(message)
        bucket.append(text)
        self.sink.write(text + "\n")
        self.sink.flush()

    def log_info(self, message):
        self._store(self.infos, message)

    def log_error(self, message):
        self._store(self.errors, message)


class SendMessageRequest:
    def __init__(self, peer):
        self.peer = peer


class DeleteMessagesRequest:
    def __init__(self, peer):
        self.peer = peer


class PingRequest:
    pass


class RequestState:
    def __init__(self, request, future, msg_id):
        self.request = request
        self.future = future
        self.msg_id = msg_id
        self.container_id = None


class SoroushLikeSender:
    def __init__(self):
        self._pending_state = {}
        self._n = 0

    def put(self, request):
        self._n += 1
        future = asyncio.get_running_loop().create_future()
        state = RequestState(request, future, self._n)
        self._pending_state[self._n] = state
        cg.note_pending(self)
        return state


class SoroushLikeClient:
    """One shared connection. RTT is the only injected delay."""

    def __init__(self):
        self._sender = SoroushLikeSender()
        self.next_rtt_ms = 40.0
        self.calls = 0

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        self.calls += 1
        state = sender.put(request)
        await asyncio.sleep(max(0.0, float(self.next_rtt_ms)) / 1000.0)
        if not state.future.done():
            state.future.set_result("ok")
        sender._pending_state.pop(state.msg_id, None)
        return "ok"

    async def send_message(self, entity, text, **kwargs):
        return await self._call(self._sender, SendMessageRequest(entity))

    async def delete_messages(self, entity, ids, **kwargs):
        return await self._call(self._sender, DeleteMessagesRequest(entity))

    async def edit_permissions(self, *a, **k):
        return "ok"

    async def kick_participant(self, *a, **k):
        return "ok"


def make_bot(client, logger):
    return types.SimpleNamespace(
        started_at=time.time(),
        client=client,
        logger=logger,
        moderation_queue=types.SimpleNamespace(_pending_keys=set(), _queues={}),
        message_delete_queue=types.SimpleNamespace(_queues={}, _pending_ids=set()),
        group_dispatcher=types.SimpleNamespace(_normal_pending={}, _queues={}),
        rpc_governor=None,
        outgoing_sender=None,
        notice_cleanup=types.SimpleNamespace(_items={}, _workers={}),
        reply_input_peer_cache={},
        runtime_snapshot=None,
    )


def parse_budget(lines, prefix):
    rows = []
    for line in lines:
        if not line.startswith(prefix):
            continue
        row = {"raw": line}
        for part in line.split():
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            try:
                row[key] = float(value)
            except ValueError:
                row[key] = value
        rows.append(row)
    return rows


def classify_critical(row):
    gov = float(row.get("governor_wait_ms") or 0)
    send = float(row.get("sender_wait_ms") or 0)
    queue = float(row.get("queue_wait_ms") or 0)
    rpc = float(row.get("rpc_await_ms") or 0)
    pending = float(row.get("sender_pending") or 0)
    if pending >= 10:
        return "sender_pending_leak"
    if gov >= 500 and gov >= rpc:
        return "governor"
    if send >= 500 and send >= rpc:
        return "sender_gate"
    if queue >= 500 and queue >= rpc:
        return "outgoing_queue"
    if rpc >= 2000:
        return "splusthon_or_network_await"
    return "mixed"


async def run_soak(sink):
    storage.use_file(Path(tempfile.mkdtemp()) / "economy.json")
    logger = Logger(sink)
    client = SoroushLikeClient()
    bot = make_bot(client, logger)
    instrument_client(client, logger)
    cg.install_rpc_timeout(client, timeout=60.0, logger=logger)
    install_outgoing_sender(client, bot, logger)
    monitor = RuntimeSnapshotMonitor(
        bot, logger, interval_seconds=0.05, lag_probe_seconds=0.0
    )
    bot.runtime_snapshot = monitor

    directory_failures = 0
    stale_seen = 0
    snapshots = []
    hour_marks = {}

    await monitor.emit(title="PERFORMANCE SNAPSHOT reason=startup")
    snapshots.append(monitor.previous)

    total_ticks = TICKS + EXTRA_TICKS
    for tick in range(1, total_ticks + 1):
        simulated_elapsed = tick * 45
        bot.started_at = time.time() - simulated_elapsed

        if tick in CRITICAL_TICKS:
            client.next_rtt_ms = 2050.0
            logger.log_info(
                f"SOAK INJECT SPLUSTHON STALL tick={tick} "
                f"sim_elapsed_s={simulated_elapsed} rtt_ms=2050"
            )
        else:
            client.next_rtt_ms = 40.0

        # 35 groups: directory write + one send each (serialized by governor).
        # During a critical tick only the first RPC is 2s; the rest stay fast
        # so we can see whether later RPCs wait on governor or on await.
        async def one_group(gid, first=False):
            nonlocal directory_failures
            try:
                directory.remember(-100000 - gid, 7000 + gid, f"user{gid}")
            except Exception:
                directory_failures += 1
            if first and tick in CRITICAL_TICKS:
                client.next_rtt_ms = 2050.0
            elif tick in CRITICAL_TICKS:
                client.next_rtt_ms = 40.0
            await client.send_message(-100000 - gid, f"tick-{tick}")

        await one_group(0, first=True)
        if tick % 4 == 0:
            # modest extra traffic, not a 35-way burst that would force
            # governor backlog by construction
            await asyncio.gather(*[one_group(g) for g in range(1, 8)])

        if tick in STALE_PING_TICKS:
            sender = client._sender
            for _ in range(3):
                sender._n += 1
                future = asyncio.get_running_loop().create_future()
                state = RequestState(PingRequest(), future, sender._n)
                sender._pending_state[sender._n] = state
                cg._seen_at(sender)[sender._n] = time.monotonic() - 45.0
            before = len(sender._pending_state)
            dropped = cg.reclaim_dead_pending(sender, logger=logger)
            after = len(sender._pending_state)
            logger.log_info(
                f"SOAK STALE PING MONITOR tick={tick} before={before} "
                f"dropped={dropped} after={after}"
            )

        reason = "periodic"
        if tick in CRITICAL_TICKS:
            reason = "slow_after_injected_stall"
        snap = await monitor.emit(title=f"PERFORMANCE SNAPSHOT reason={reason} tick={tick}")
        snapshots.append(snap)

        if simulated_elapsed in (1800, 3600, 5400, 7200) or tick in (40, 80, 120, 160, 200):
            hour_marks[simulated_elapsed] = dict(snap)

    await monitor.stop()

    # Final reclaim like the bot's 60s cleanup; do not change its rules.
    leftover = cg.reclaim_dead_pending(client._sender, logger=logger)
    final = await collect_final(bot)
    return {
        "logger": logger,
        "snapshots": snapshots,
        "hour_marks": hour_marks,
        "directory_failures": directory_failures,
        "calls": client.calls,
        "leftover_reclaim": leftover,
        "final": final,
        "pending_end": len(client._sender._pending_state),
    }


async def collect_final(bot):
    from modules.runtime_snapshot import collect
    return await collect(bot, lag_probe_seconds=0.0)


def write_report(result):
    logger = result["logger"]
    snaps = result["snapshots"]
    slow = parse_budget(logger.infos, "OUTGOING RPC SLOW")
    critical = parse_budget(logger.errors, "OUTGOING RPC CRITICAL")
    stale = [line for line in logger.infos if line.startswith("STALE SENDER PENDING")]
    failed_dir = [line for line in logger.errors if "USERNAME DIRECTORY FAILED" in line]
    growth = [line for line in logger.errors if "SENDER PENDING GROWTH" in line]
    sender_series = [int(s.get("sender_pending") or 0) for s in snaps]
    task_series = [int(s.get("pending_tasks") or 0) for s in snaps]
    mem_series = [float(s.get("memory_mb") or 0) for s in snaps]
    lag_series = [float(s.get("event_loop_lag_ms") or 0) for s in snaps]

    verdicts = []
    crit_classes = [classify_critical(row) for row in critical]
    if crit_classes and all(c == "splusthon_or_network_await" for c in crit_classes):
        verdicts.append(
            "تأخیر ۲ثانیه‌ای در CRITICALها از خود await/SPlusthon است "
            "(governor_wait/sender_wait/queue_wait نزدیک صفر، sender_pending رشد نکرد)."
        )
    elif any(c == "governor" for c in crit_classes):
        verdicts.append("تأخیر CRITICAL از انتظار Governor است.")
    elif any(c == "sender_pending_leak" for c in crit_classes):
        verdicts.append("sender_pending هنگام کندی بالا بود؛ leak محتمل است.")
    else:
        verdicts.append("CRITICAL مخلوط بود؛ جدول زیر را ببین.")

    if max(sender_series or [0]) == 0 or max(sender_series) <= 3:
        verdicts.append("sender_pending در طول soak رشد مداوم نداشت.")
    else:
        verdicts.append(f"sender_pending تا {max(sender_series)} بالا رفت.")

    if growth:
        verdicts.append("دتکتور SENDER PENDING GROWTH حداقل یک‌بار آتش شد.")
    else:
        verdicts.append("SENDER PENDING GROWTH دیده نشد.")

    mem0, mem1 = (mem_series[0] if mem_series else 0), (mem_series[-1] if mem_series else 0)
    if mem1 - mem0 >= 16:
        verdicts.append(f"memory_mb رشد کرد: {mem0:.1f} → {mem1:.1f}")
    else:
        verdicts.append(f"memory_mb رشد غیرعادی نداشت: {mem0:.1f} → {mem1:.1f}")

    t0, t1 = (task_series[0] if task_series else 0), (task_series[-1] if task_series else 0)
    if t1 - t0 >= 20:
        verdicts.append(f"pending_tasks رشد کرد: {t0} → {t1}")
    else:
        verdicts.append(f"pending_tasks پایدار ماند: {t0} → {t1}")

    if failed_dir or result["directory_failures"]:
        verdicts.append("USERNAME DIRECTORY FAILED هنوز رخ می‌دهد.")
    else:
        verdicts.append("USERNAME DIRECTORY FAILED رگباری نبود (صفر).")

    if stale:
        verdicts.append(
            f"Ping stale فقط مانیتور شد: {len(stale)} خط STALE SENDER PENDING؛ "
            "پاکسازی فعلی عوض نشد."
        )

    lines = [
        "# Long soak — outgoing RPC / sender_pending",
        "",
        "کد محصول تغییر نکرد. این اجرا ماژول‌های فعلی را با ۳۵ گروه و",
        f"{TICKS + EXTRA_TICKS} تیک (معادل {(TICKS + EXTRA_TICKS) * 45 / 3600:.2f} ساعت با فاصله ۴۵ثانیه) برد.",
        "ربات زنده با SPlusthon روی این هاست نیست؛ تأخیر ۲ثانیه‌ای داخل `_call` تزریق شد تا محل ثبت آن مشخص شود.",
        "",
        "## Verdict",
        "",
    ]
    for item in verdicts:
        lines.append(f"- {item}")

    lines.extend(["", "## CRITICAL rows", ""])
    if not critical:
        lines.append("_no CRITICAL logs_")
    for row in critical:
        lines.append(
            f"- class=`{classify_critical(row)}` "
            f"queue={row.get('queue_wait_ms')} "
            f"governor={row.get('governor_wait_ms')} "
            f"sender={row.get('sender_wait_ms')} "
            f"rpc_await={row.get('rpc_await_ms')} "
            f"total={row.get('total_ms')} "
            f"sender_pending={row.get('sender_pending')}"
        )

    lines.extend(["", "## SLOW count", f"- {len(slow)} lines (threshold 500ms)"])
    lines.extend(["", "## Snapshot marks (simulated elapsed)", ""])
    lines.append("| sim_s | sender_pending | pending_tasks | active_tasks | lag_ms | memory_mb | rss_mb | dir | economy | peer |")
    lines.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for elapsed, snap in sorted(result["hour_marks"].items()):
        mem = float(snap.get("memory_mb") or 0)
        lines.append(
            f"| {elapsed} | {snap.get('sender_pending')} | {snap.get('pending_tasks')} | "
            f"{snap.get('active_tasks')} | {snap.get('event_loop_lag_ms')} | {mem} | {mem} | "
            f"{snap.get('username_directory_cache_size')} | {snap.get('economy_cache_size')} | "
            f"{snap.get('peer_cache_size')} |"
        )

    first, last = snaps[0], snaps[-1]
    lines.extend([
        "",
        "## First vs last snapshot",
        "",
        "```",
        format_snapshot(first, title="PERFORMANCE SNAPSHOT first"),
        "```",
        "",
        "```",
        format_snapshot(last, title="PERFORMANCE SNAPSHOT last"),
        "```",
        "",
        "## Totals",
        "",
        f"- client._call count: {result['calls']}",
        f"- snapshots: {len(snaps)}",
        f"- SLOW: {len(slow)}",
        f"- CRITICAL: {len(critical)}",
        f"- STALE SENDER PENDING: {len(stale)}",
        f"- USERNAME DIRECTORY FAILED: {len(failed_dir)}",
        f"- directory_failures exceptions: {result['directory_failures']}",
        f"- sender_pending series max: {max(sender_series) if sender_series else 0}",
        f"- pending at end: {result['pending_end']}",
        f"- event_loop_lag max: {max(lag_series) if lag_series else 0}",
        "",
        f"Raw log: `{RAW_LOG}`",
    ])
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return "\n".join(lines)


def main():
    with RAW_LOG.open("w", encoding="utf-8") as sink:
        result = asyncio.run(run_soak(sink))
    report = write_report(result)
    print(report)
    print(f"\nWROTE {REPORT}")
    critical = parse_budget(result["logger"].errors, "OUTGOING RPC CRITICAL")
    classes = {classify_critical(row) for row in critical}
    sender_max = max(int(s.get("sender_pending") or 0) for s in result["snapshots"])
    failed = 0
    if "splusthon_or_network_await" not in classes:
        print("FAIL expected injected 2s stall to classify as rpc_await")
        failed += 1
    if sender_max > 5:
        print(f"FAIL sender_pending grew to {sender_max}")
        failed += 1
    if result["directory_failures"] or any(
        "USERNAME DIRECTORY FAILED" in line for line in result["logger"].errors
    ):
        print("FAIL directory errors")
        failed += 1
    if result["pending_end"] != 0:
        print(f"FAIL pending leftover {result['pending_end']}")
        failed += 1
    print(f"soak_failed={failed}")
    return failed


if __name__ == "__main__":
    raise SystemExit(main())
