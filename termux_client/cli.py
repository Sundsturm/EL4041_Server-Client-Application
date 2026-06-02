"""
cli.py
Interactive CLI for Android/Termux client.
"""

from __future__ import annotations

from core.command_controller import CommandController


async def run_cli(controller: CommandController) -> None:
    print("=" * 48)
    print(" STP MUSIC TERMUX CLIENT")
    print(" Type 'help' for commands.")
    print("=" * 48)

    running = True
    while running:
        try:
            line = input("stp> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        running = await controller.execute(line)
