from __future__ import annotations

import json
import os
import signal
import time
import unittest
from unittest.mock import patch

import semantic


class _HeartbeatResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def __iter__(self):
        return iter([b": heartbeat\n"])


class SemanticTimeoutTest(unittest.TestCase):
    @unittest.skipUnless(
        hasattr(signal, "setitimer"), "hard deadlines require POSIX timers"
    )
    def test_hard_deadline_interrupts_blocking_read(self):
        started = time.monotonic()
        with self.assertRaisesRegex(TimeoutError, "整体时限 1s"):
            with semantic._hard_deadline(1):
                time.sleep(5)
        self.assertLess(time.monotonic() - started, 2)

    def test_stream_has_deadline_and_disables_thinking_by_default(self):
        env = {
            "ARK_BASE_URL": "https://ark.example/api/v3",
            "ARK_API_KEY": "test-key",
            "TOPIC5_SEMANTIC_TIMEOUT_SEC": "10",
            "TOPIC5_SEMANTIC_IDLE_TIMEOUT_SEC": "3",
        }
        with (
            patch.dict(os.environ, env, clear=False),
            patch.object(
                semantic.urllib.request,
                "urlopen",
                return_value=_HeartbeatResponse(),
            ) as urlopen,
            patch.object(
                semantic.time,
                "monotonic",
                side_effect=[0.0, 11.0],
            ),
        ):
            with self.assertRaisesRegex(TimeoutError, "整体时限 10s"):
                semantic.chat("system", "user", retries=1)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 3)
        request_body = json.loads(urlopen.call_args.args[0].data)
        self.assertEqual(request_body["thinking"], {"type": "disabled"})


if __name__ == "__main__":
    unittest.main()
