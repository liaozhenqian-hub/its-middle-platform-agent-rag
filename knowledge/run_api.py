"""Application server entry point."""

from __future__ import annotations

import asyncio
import sys

import uvicorn


def configure_windows_event_loop_policy() -> bool:
    """Use the event loop required by psycopg async connections on Windows."""
    if sys.platform != "win32":
        return False
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return False
    asyncio.set_event_loop_policy(policy_factory())
    return True


def api_loop_factory() -> asyncio.AbstractEventLoop:
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return asyncio.new_event_loop()


def main() -> None:
    configure_windows_event_loop_policy()
    uvicorn.run(
        "knowledge.api.app:app",
        host="0.0.0.0",
        port=8000,
        workers=1,
        loop=api_loop_factory,
    )


if __name__ == "__main__":
    main()
