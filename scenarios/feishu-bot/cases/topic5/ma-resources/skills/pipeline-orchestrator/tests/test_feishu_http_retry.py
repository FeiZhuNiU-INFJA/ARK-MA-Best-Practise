"""飞书发布限流恢复：复现 429/503，并验证重放边界。"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import feishu


class TestFeishuHttpRetry(unittest.TestCase):
    def error(self, code, retry_after=None):
        headers = {} if retry_after is None else {"Retry-After": retry_after}
        return urllib.error.HTTPError(
            "https://open.feishu.cn/open-apis/docx/v1/documents/doc/blocks/block",
            code, "request rejected", headers, io.BytesIO(b"{}"),
        )

    def response(self):
        return io.BytesIO(json.dumps({"code": 0, "data": {"block_id": "done"}}).encode())

    def test_req_retries_rate_limited_write_with_same_body(self):
        error = self.error(429, "2")
        with patch("feishu.urllib.request.urlopen", side_effect=[error, self.response()]) as send, \
                patch("feishu.time.sleep") as sleep:
            body = {"children": [{"block_type": 2}], "index": 0}
            result = feishu._req("POST", "/blocks/children", "test-token", body)
        self.assertEqual(result, {"block_id": "done"})
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[0].data,
                         send.call_args_list[1].args[0].data)
        sleep.assert_called_once_with(2)
        self.assertTrue(error.fp.closed)

    def test_req_recovers_read_service_unavailable(self):
        with patch("feishu.urllib.request.urlopen",
                   side_effect=[self.error(503), self.response()]) as send, \
                patch("feishu.time.sleep") as sleep:
            self.assertEqual(feishu._req("GET", "/blocks/block", "test-token"),
                             {"block_id": "done"})
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_req_recovers_idempotent_text_update(self):
        body = {"update_text_elements": {"elements": []}}
        with patch("feishu.urllib.request.urlopen",
                   side_effect=[self.error(503), self.response()]) as send, \
                patch("feishu.time.sleep") as sleep:
            self.assertEqual(feishu._req("PATCH", "/blocks/block", "test-token", body),
                             {"block_id": "done"})
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_req_does_not_replay_ambiguous_create_or_insert(self):
        for method, body in (
            ("POST", {"children": []}),
            ("PATCH", {"insert_table_row": {"row_index": -1}}),
        ):
            with self.subTest(method=method), \
                    patch("feishu.urllib.request.urlopen", side_effect=self.error(503)) as send, \
                    patch("feishu.time.sleep") as sleep:
                with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
                    feishu._req(method, "/blocks/block", "test-token", body)
                send.assert_called_once()
                sleep.assert_not_called()

    def test_req_stops_after_bounded_rate_limit_attempts(self):
        errors = [self.error(429) for _ in range(6)]
        with patch("feishu.urllib.request.urlopen", side_effect=errors) as send, \
                patch("feishu.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                feishu._req("GET", "/blocks/block", "test-token")
        self.assertEqual(send.call_count, 6)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4, 8, 16])

    def test_req_rejects_retry_after_beyond_budget(self):
        with patch("feishu.urllib.request.urlopen", side_effect=self.error(429, "120")) as send, \
                patch("feishu.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 429"):
                feishu._req("GET", "/blocks/block", "test-token")
        send.assert_called_once()
        sleep.assert_not_called()

    def test_req_invalid_retry_after_uses_backoff(self):
        with patch("feishu.urllib.request.urlopen",
                   side_effect=[self.error(429, "invalid"), self.response()]) as send, \
                patch("feishu.time.sleep") as sleep:
            self.assertEqual(feishu._req("GET", "/blocks/block", "test-token"),
                             {"block_id": "done"})
        self.assertEqual(send.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_req_does_not_retry_permission_failure(self):
        with patch("feishu.urllib.request.urlopen", side_effect=self.error(403)) as send, \
                patch("feishu.time.sleep") as sleep:
            with self.assertRaisesRegex(RuntimeError, "HTTP 403"):
                feishu._req("GET", "/blocks/block", "test-token")
        send.assert_called_once()
        sleep.assert_not_called()
