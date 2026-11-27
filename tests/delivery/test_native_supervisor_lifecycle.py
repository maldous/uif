"""Exercise UIF's real supervised lifecycle without starting a scheduler."""
import asyncio
import os
from pathlib import Path
import signal
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch

from uif.stokowski_cloudflare.launcher import _start_with_shutdown


class NativeSupervisorLifecycle(unittest.IsolatedAsyncioTestCase):
    async def test_partial_signal_setup_failure_does_not_leave_handler_or_start(self):
        loop = asyncio.get_running_loop()
        start = AsyncMock()
        with (patch.object(loop, 'add_signal_handler', side_effect=[None, OSError('fixture')]),
              patch.object(loop, 'remove_signal_handler') as remove):
            with self.assertRaisesRegex(OSError, 'fixture'):
                await _start_with_shutdown(object(), start)
        start.assert_not_called()
        remove.assert_called_once_with(signal.SIGTERM)

    async def test_service_signal_stops_running_owner_and_cleans_handlers(self):
        loop = asyncio.get_running_loop()
        handlers = {}
        finished = asyncio.Event()
        owner = type('Owner', (), {})()
        owner.stop = AsyncMock(side_effect=finished.set)

        async def start(argument):
            self.assertIs(argument, owner)
            loop.call_soon(handlers[signal.SIGTERM])
            await finished.wait()
            return 'stopped'

        with (patch.object(loop, 'add_signal_handler',
                           side_effect=lambda sig, handler: handlers.update({sig: handler})),
              patch.object(loop, 'remove_signal_handler', side_effect=handlers.pop)):
            self.assertEqual(await asyncio.wait_for(_start_with_shutdown(owner, start), 2), 'stopped')
        owner.stop.assert_awaited_once_with()
        self.assertEqual(handlers, {})

    def test_real_idle_process_exits_cleanly_on_sigterm_without_stock(self):
        src = str(Path(__file__).resolve().parents[2] / 'src')
        env = {key: value for key, value in os.environ.items() if key != 'NOTIFY_SOCKET'}
        command = [sys.executable, '-I', '-S', '-B', '-c',
                   'import sys; sys.path.insert(0, sys.argv[1]); '
                   'from uif.stokowski_cloudflare.launcher import serve_supervised_idle; '
                   'assert "stokowski" not in sys.modules; '
                   'serve_supervised_idle("free_route_unavailable")', src]
        with subprocess.Popen(command, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                # Wait for the real process's status after signal setup.
                import select
                self.assertTrue(select.select([process.stdout], [], [], 5)[0])
                self.assertEqual(process.stdout.readline().strip(),
                                 'Stokowski idle: free_route_unavailable; admissions and effects closed')
                self.assertIsNone(process.poll())
                process.terminate()
                out, err = process.communicate(timeout=5)
                self.assertEqual(process.returncode, 0, err)
                self.assertEqual(out.strip(), 'Stokowski idle shutdown')
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=5)
