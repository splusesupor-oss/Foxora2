"""Simulated high-load regression for RPC Governor across 60 groups."""
import asyncio
import time
from modules.rpc_governor import (
    RpcGovernor,
    RpcAdmission,
    RpcOverloadError,
    P0_CRITICAL,
    P1_DELETE,
    P2_SEND,
    P3_HEAVY,
)

def test_critical_operations_never_starve_under_60_groups_load():
    async def scenario():
        governor = RpcGovernor(
            total_limit=3,
            noncritical_limit=2,
            delete_limit=2,
            send_limit=2,
            heavy_limit=1,
            max_send_waiters=20,
        )

        # 60 groups spamming normal/entertainment sends
        send_tasks = []
        rejected_sends = 0
        admitted_sends = []

        async def send_worker(chat_id):
            nonlocal rejected_sends
            adm = RpcAdmission(P2_SEND, "send", "SendMessageRequest", str(chat_id))
            try:
                permit = await governor.acquire(adm)
                admitted_sends.append(chat_id)
                await asyncio.sleep(0.02)
                permit.release()
            except RpcOverloadError:
                rejected_sends += 1

        for chat in range(1, 61):
            send_tasks.append(asyncio.create_task(send_worker(chat)))

        await asyncio.sleep(0.005)

        # High priority ban/mute/delete arrives while 60 groups are competing
        t_start = time.perf_counter()
        crit_adm = RpcAdmission(P0_CRITICAL, "critical", "EditBannedRequest", "chat_admin")
        crit_permit = await asyncio.wait_for(governor.acquire(crit_adm), timeout=0.1)
        wait_ms = (time.perf_counter() - t_start) * 1000

        # Critical operation gets admitted immediately via reserved slot!
        assert crit_permit is not None
        assert wait_ms < 50.0  # Must be fast, never starved

        crit_permit.release()

        # Delete message arrives
        del_adm = RpcAdmission(P1_DELETE, "delete", "DeleteMessagesRequest", "chat_spam")
        del_permit = await asyncio.wait_for(governor.acquire(del_adm), timeout=0.1)
        del_permit.release()

        await asyncio.gather(*send_tasks, return_exceptions=True)

        snap = governor.snapshot()
        assert snap["active"] == 0
        assert snap["waiting"] == 0
        assert rejected_sends > 0  # Load shedding actively protected the governor!
        print(f"High load test passed! Admitted sends: {len(admitted_sends)}, Shed/Throttled sends: {rejected_sends}, Critical wait: {wait_ms:.2f}ms")

    asyncio.run(scenario())

if __name__ == "__main__":
    test_critical_operations_never_starve_under_60_groups_load()
