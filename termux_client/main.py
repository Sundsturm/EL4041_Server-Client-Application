"""
main.py
Entry point for Android/Termux CLI client.
Transport: CSP/QUIC (aioquic) — satu-satunya mode yang didukung.
"""

from __future__ import annotations

import asyncio

from core.auth_manager import AuthManager
from core.local_storage import ensure_dirs
from core.command_controller import CommandController
from core.csp_client import CSPClient
from cli import run_cli


async def async_main() -> None:
    ensure_dirs()
    auth = AuthManager()
    api  = CSPClient(auth)

    controller = CommandController(api, auth)

    try:
        await run_cli(controller)
    finally:
        # CSPClient tidak memiliki persistent connection, tidak ada close() yang diperlukan.
        pass


if __name__ == "__main__":
    asyncio.run(async_main())
