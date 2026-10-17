import asyncio

DELETE_QUEUE = []
DELETE_LOCK = asyncio.Lock()


async def add_delete(chat_id, message_id):
    async with DELETE_LOCK:
        DELETE_QUEUE.append(
            (chat_id, message_id)
        )


async def process_delete(bot):
    while True:
        await asyncio.sleep(0.2)

        async with DELETE_LOCK:
            if not DELETE_QUEUE:
                continue

            items = DELETE_QUEUE[:100]
            del DELETE_QUEUE[:100]

        groups = {}

        for chat_id, msg_id in items:
            groups.setdefault(chat_id, []).append(msg_id)

        for chat_id, ids in groups.items():
            try:
                await bot.client.delete_messages(
                    chat_id,
                    ids
                )
            except Exception as e:
                print("delete queue error:", e)
