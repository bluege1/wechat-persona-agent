"""验证主程序使用的发送协议，只到入队为止，不加载微信自动化模块。"""

import json
from pathlib import Path
from queue import Queue
import sys
import threading
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "小信使"))

from src.network.http_server import _CommandRequestHandler, _ThreadingHTTPServer


class SenderServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.queue = Queue()
        cls.previous_queue = _CommandRequestHandler.send_queue
        _CommandRequestHandler.send_queue = cls.queue
        cls.server = _ThreadingHTTPServer(("127.0.0.1", 0), _CommandRequestHandler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = "http://127.0.0.1:%d" % cls.server.server_port
        cls.client = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
            cls.server.server_close()
            cls.thread.join(timeout=3)
        finally:
            _CommandRequestHandler.send_queue = cls.previous_queue

    def request(self, path, payload=None, raw=None):
        data = json.dumps(payload).encode("utf-8") if payload is not None else raw
        req = urllib.request.Request(
            self.url + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            response = self.client.open(req, timeout=3)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            return response.status, json.load(response)

    def test_health_does_not_enqueue(self):
        self.assertEqual(self.request("/health"), (200, {"status": "ok"}))
        self.assertTrue(self.queue.empty())

    def test_text_preserves_refocus_for_frontend_fallback(self):
        payload = {
            "type": "send_message", "target": "测试会话",
            "message": "测试文字", "refocus": True,
        }
        status, _ = self.request("/", payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.queue.get(timeout=1), payload)

    def test_voice_accepts_both_format_field_names(self):
        for field in ("format", "voice_format"):
            with self.subTest(field=field):
                payload = {
                    "type": "send_voice", "target": "测试会话",
                    "voice_data": "dGVzdA==", field: "wav",
                }
                status, _ = self.request("/api/message", payload)
                self.assertEqual(status, 200)
                command = self.queue.get(timeout=1)
                self.assertEqual(command["type"], "send_voice")
                self.assertEqual(command["voice_data"], payload["voice_data"])
                self.assertEqual(command["voice_format"], "wav")

    def test_sticker_payload_reaches_queue(self):
        payload = {
            "type": "send_sticker", "target": "测试会话",
            "sticker_data": "dGVzdA==",
        }
        status, _ = self.request("/", payload)
        self.assertEqual(status, 200)
        self.assertEqual(self.queue.get(timeout=1), payload)

    def test_invalid_commands_are_rejected_without_enqueue(self):
        for payload in ([], {"type": "send_message"},
                        {"type": "send_message", "target": "测试会话"},
                        {"type": "unknown", "target": "测试会话"}):
            with self.subTest(payload=payload):
                status, _ = self.request("/", payload)
                self.assertEqual(status, 400)
                self.assertTrue(self.queue.empty())

    def test_malformed_json_is_rejected_without_enqueue(self):
        status, _ = self.request("/", raw=b"{invalid")
        self.assertEqual(status, 400)
        self.assertTrue(self.queue.empty())

    def test_unknown_path_does_not_enqueue(self):
        status, _ = self.request("/missing", {"type": "send_message"})
        self.assertEqual(status, 404)
        self.assertTrue(self.queue.empty())


if __name__ == "__main__":
    unittest.main()
