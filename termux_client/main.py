"""
main.py
Entry point for Android/Termux CLI client.
"""

from __future__ import annotations

import argparse
import asyncio

from core.auth_manager import AuthManager
from core.local_storage import ensure_dirs
from core.command_controller import CommandController
from core.csp_client import CSPClient
from core.rest_client import RESTClient
from cli import run_cli


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="STP Music Termux Client")
    parser.add_argument(
        "--mode",
        choices=["csp", "rest"],
        default="csp",
        help="Use CSP/QUIC mode or REST fallback mode. Default: csp",
    )
    args = parser.parse_args()

    ensure_dirs()
    auth = AuthManager()

    if args.mode == "rest":
        api = RESTClient(auth)
    else:
        api = CSPClient(auth)

    controller = CommandController(api, auth)

    try:
        await run_cli(controller)
    finally:
        close = getattr(api, "close", None)
        if close:
            await close()


if __name__ == "__main__":
    asyncio.run(async_main())
