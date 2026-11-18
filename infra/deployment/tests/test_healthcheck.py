import json
import sys
import unittest

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from unittest.mock import patch

sys.path.insert(
    0, str(Path(__file__).resolve().parents[1])
)

import healthcheck

SHA = "a" * 40


class Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(self.server.test_status)
        self.send_header(
            "Content-Type", "application/json"
        )
        self.end_headers()
        self.wfile.write(self.server.test_body)

    def log_message(self, *args):
        pass


class HealthcheckTests(unittest.TestCase):

    def setUp(self):
        self.server = HTTPServer(
            ("127.0.0.1", 0), Handler
        )

        self.port = self.server.server_port

        self.server.test_status = 200
        self.server.test_body = json.dumps({
            "status": "ready",
            "release": SHA,
        }).encode()

        self.thread = Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )

        self.thread.start()
        self.addCleanup(self.stop_server)

    def stop_server(self):
        self.server.shutdown()
        self.thread.join()
        self.server.server_close()

    def probe(self):
        return healthcheck.probe(SHA, self.port)

    def test_ready_release(self):
        self.assertTrue(self.probe())

    def test_http_503(self):
        self.server.test_status = 503
        self.assertFalse(self.probe())

    def test_wrong_release(self):
        self.server.test_body = json.dumps({
            "status": "ready",
            "release": "b" * 40,
        }).encode()

        self.assertFalse(self.probe())

    def test_not_ready_with_http_200(self):
        self.server.test_body = json.dumps({
            "status": "not_ready",
            "release": SHA,
        }).encode()

        self.assertFalse(self.probe())

    def test_invalid_json(self):
        self.server.test_body = b"invalid"
        self.assertFalse(self.probe())

    def test_oversized_response(self):
        self.server.test_body = b"x" * 4097
        self.assertFalse(self.probe())

    def test_three_consecutive_successes(self):
        with patch(
            "healthcheck.probe",
            side_effect=[
                True, False, True, True, True
            ],
        ) as probe:

            result = healthcheck.wait_ready(
                SHA,
                timeout=2,
                interval=0,
                grace_period=0,
            )

            self.assertTrue(result)
            self.assertEqual(probe.call_count, 5)

    def test_health_timeout(self):
        with patch(
            "healthcheck.probe",
            return_value=False,
        ):

            result = healthcheck.wait_ready(
                SHA,
                timeout=0.05,
                interval=0.01,
                grace_period=0,
            )

            self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
