from __future__ import annotations

import argparse
import asyncio

from foxbot.runtime import RuntimeSettings, create_soroush_client


async def initialize_session(settings: RuntimeSettings | None = None) -> str:
    """Interactively initialize the local SPlusthon SQLite session file."""
    settings = settings or RuntimeSettings.from_env()
    if settings.session_string:
        raise ValueError(
            "Session initialization requires SOROUSH_SESSION_PATH; do not print or store a StringSession here"
        )
    client = create_soroush_client(settings)
    try:
        await client.start()
        me = await client.get_me()
        return str(me.id)
    finally:
        await client.disconnect()


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="fox-soroush-login",
        description="Initialize a local SPlusthon session from an interactive terminal.",
    )
    parser.parse_args()
    user_id = asyncio.run(initialize_session())
    print(f"SPlusthon session initialized for account ID {user_id}.")


if __name__ == "__main__":
    main()
