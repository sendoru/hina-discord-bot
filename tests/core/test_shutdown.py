import signal
import sqlite3
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from hina_bot.core.store import Store
from hina_bot.discord.shutdown import _sigterm_handler, run_client


class SignalShutdownTests(unittest.TestCase):
    def test_sigterm_handler_raises_keyboard_interrupt(self):
        with self.assertRaises(KeyboardInterrupt):
            _sigterm_handler(signal.SIGTERM, None)

    def test_runner_installs_and_restores_sigterm_handler(self):
        bot = NS(run=Mock())
        previous = object()

        with (
            patch("hina_bot.discord.shutdown.signal.getsignal", return_value=previous),
            patch("hina_bot.discord.shutdown.signal.signal") as install,
        ):
            run_client(bot, "token")

        bot.run.assert_called_once_with("token", log_handler=None)
        self.assertEqual(
            install.call_args_list,
            [
                unittest.mock.call(signal.SIGTERM, _sigterm_handler),
                unittest.mock.call(signal.SIGTERM, previous),
            ],
        )

    def test_runner_restores_handler_when_client_run_fails(self):
        bot = NS(run=Mock(side_effect=RuntimeError("boom")))
        previous = object()

        with (
            patch("hina_bot.discord.shutdown.signal.getsignal", return_value=previous),
            patch("hina_bot.discord.shutdown.signal.signal") as install,
            self.assertRaises(RuntimeError),
        ):
            run_client(bot, "token")

        self.assertEqual(install.call_args_list[-1], unittest.mock.call(signal.SIGTERM, previous))


class StoreShutdownTests(unittest.TestCase):
    def test_close_checkpoints_wal_before_closing_connection(self):
        events = []

        class FakeConnection:
            def execute(self, sql):
                events.append(("execute", sql))

            def close(self):
                events.append(("close", None))

        store = object.__new__(Store)
        store.db = FakeConnection()
        store.close()

        self.assertEqual(
            events,
            [
                ("execute", "PRAGMA wal_checkpoint(TRUNCATE)"),
                ("close", None),
            ],
        )

    def test_close_still_closes_when_checkpoint_fails(self):
        events = []

        class FakeConnection:
            def execute(self, sql):
                events.append(("execute", sql))
                raise sqlite3.OperationalError("busy")

            def close(self):
                events.append(("close", None))

        store = object.__new__(Store)
        store.db = FakeConnection()
        store.close()

        self.assertEqual(events[-1], ("close", None))


if __name__ == "__main__":
    unittest.main()
