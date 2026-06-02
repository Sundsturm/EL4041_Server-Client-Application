"""
cli.py
Interactive CLI for Android/Termux client.
"""

from __future__ import annotations

from core.command_controller import CommandController


async def run_cli(controller: CommandController) -> None:
    print("=" * 52)
    print("  ♪  STP MUSIC — TERMUX CLIENT")
    print("  Type 'help' for a list of commands.")
    print("=" * 52)

    running = True
    while running:
        # Dynamic prompt: show @username when logged in, else "stp"
        if controller.auth.is_logged_in():
            prompt = f"@{controller.auth.get_username()}> "
        else:
            prompt = "stp> "

        try:
            line = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("")
            break
        running = await controller.execute(line)
