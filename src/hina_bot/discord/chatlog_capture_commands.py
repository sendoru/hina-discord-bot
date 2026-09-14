"""Backward-compatible installer shim for the retired /chatlog capture command.

Capture scope is now part of /chatlog mode (all/direct/off/inherit). Keep this no-op while older
runtime wiring still imports the installer; it can be removed in a later cleanup without changing
the command surface.
"""


def install_chatlog_capture(client) -> None:
    return None


__all__ = ["install_chatlog_capture"]
