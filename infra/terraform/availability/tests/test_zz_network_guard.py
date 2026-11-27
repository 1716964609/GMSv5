"""The test process cannot reach the network (runs last in discovery order)."""
import os as _os, sys as _sys  # noqa: E401
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import no_network  # noqa: E402  Blocks real network access (no AWS calls).
import socket  # noqa: E402
import unittest  # noqa: E402


class NetworkGuardTests(unittest.TestCase):
    def test_a_real_aws_call_is_blocked_before_any_connection(self):
        import boto3
        from botocore.config import Config
        before = len(no_network.ATTEMPTS)
        client = boto3.Session(aws_access_key_id="blocked", aws_secret_access_key="blocked",
                               region_name="ap-northeast-1").client(
            "cloudwatch", config=Config(retries={"total_max_attempts": 1}))
        with self.assertRaises(Exception):
            client.list_metrics(Namespace="GMS/Availability")
        attempts = no_network.ATTEMPTS[before:]
        self.assertTrue(attempts)
        self.assertTrue(all("amazonaws.com" in host for _, host in attempts), attempts)
        del no_network.ATTEMPTS[before:]  # expected; the next test checks the rest

    def test_b_raw_sockets_are_blocked(self):
        before = len(no_network.ATTEMPTS)
        with self.assertRaises(no_network.NetworkBlocked):
            socket.create_connection(("monitoring.ap-northeast-1.amazonaws.com", 443), timeout=1)
        with self.assertRaises(no_network.NetworkBlocked):
            socket.getaddrinfo("dynamodb.ap-northeast-1.amazonaws.com", 443)
        del no_network.ATTEMPTS[before:]

    def test_z_suite_made_no_network_attempt(self):
        self.assertEqual(no_network.ATTEMPTS, [])
        self.assertTrue(socket._gmsv5_network_blocked)


if __name__ == "__main__":
    unittest.main()
