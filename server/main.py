"""
server/main.py
---------------
Entry point for the Hybrid Music Sharing Server.

Starts both servers concurrently on a single asyncio event loop:
  - REST/HTTPS   (FastAPI + Uvicorn)  on TCP port 8443
  - CSP/QUIC     (aioquic)            on UDP port 4433

Also runs the session-expiry cleanup coroutine as a background task.
Handles graceful shutdown on Ctrl-C / SIGINT.
"""

import asyncio
import signal
import sys

from server import config
from server.database import close_db, init_db
from server.network.quic_server import run_quic_server
from server.network.rest_server import build_uvicorn_server
from server.services.session_service import cleanup_loop


async def main() -> None:
    print("=" * 55)
    print("  Hybrid Music Sharing Server")
    print(f"  REST/HTTPS  ->  tcp://{config.REST_HOST}:{config.REST_PORT}")
    print(f"  CSP/QUIC    ->  udp://{config.QUIC_HOST}:{config.QUIC_PORT}")
    print(f"  Tailscale hostname: {config.TAILSCALE_HOSTNAME}")
    print("=" * 55)

    # 1. Initialise database (idempotent)
    await init_db()
    print("[DB] Schema initialised.")

    # 2. Build REST server (does not start yet)
    uvicorn_server = build_uvicorn_server()

    # 3. Create a shutdown event to coordinate teardown
    shutdown_event = asyncio.Event()

    def _signal_handler() -> None:
        print("\n[Server] Shutdown signal received.")
        shutdown_event.set()

    loop = asyncio.get_event_loop()
    if sys.platform != "win32":
        # POSIX signal handling
        loop.add_signal_handler(signal.SIGINT, _signal_handler)
        loop.add_signal_handler(signal.SIGTERM, _signal_handler)

    # 4. Start all tasks concurrently
    tasks = [
        asyncio.create_task(uvicorn_server.serve(), name="rest-https"),
        asyncio.create_task(run_quic_server(), name="csp-quic"),
        asyncio.create_task(cleanup_loop(), name="session-cleanup"),
        asyncio.create_task(shutdown_event.wait(), name="shutdown-watcher"),
    ]

    print("[Server] All services started. Press Ctrl-C to stop.")

    # Wait until the shutdown event fires (or any task raises an exception)
    done, pending = await asyncio.wait(
        tasks,
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Check for unexpected errors
    for task in done:
        if task.get_name() != "shutdown-watcher":
            exc = task.exception()
            if exc:
                print(f"[ERROR] Task '{task.get_name()}' raised: {exc!r}")

    # 5. Cancel remaining tasks
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)

    # 6. Close DB connection
    await close_db()
    print("[Server] Shutdown complete.")


if __name__ == "__main__":
    # Windows needs this to handle Ctrl-C cleanly with asyncio
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
