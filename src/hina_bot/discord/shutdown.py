import signal


def _sigterm_handler(_signum, _frame):
    """Route SIGTERM through discord.py's KeyboardInterrupt shutdown path."""
    raise KeyboardInterrupt


def run_client(bot, token: str) -> None:
    """Run a discord.py client while treating SIGTERM like a normal Ctrl+C shutdown."""
    previous = signal.getsignal(signal.SIGTERM)
    signal.signal(signal.SIGTERM, _sigterm_handler)
    try:
        bot.run(token, log_handler=None)
    finally:
        signal.signal(signal.SIGTERM, previous)


__all__ = ["run_client"]
