import struct
import time
import unittest
from unittest.mock import Mock

from experiments import deep_send_probe as probe


def document(elements):
    return struct.pack("<i", len(elements) + 5) + elements + b"\0"


class DeepSendProbeTests(unittest.TestCase):
    def test_only_outgoing_packet_is_anonymous_getconf(self):
        packet = probe.booking_request()
        packet_id, status, method, body_type, size = probe.HEADER.unpack(packet[:22])
        self.assertEqual((packet_id, status, method.rstrip(b"\0"), body_type), (1, 0, b"GETCONF", 0))
        self.assertEqual(size, len(packet) - 22)
        self.assertEqual(probe.decode_bson(packet[22:]), {
            "MCCMNC": "99999", "os": "mac", "model": "", "userId": 0,
        })

    def test_nested_routes_and_arrays_decode(self):
        routes = document(b"\x020\0" + struct.pack("<i", 5) + b"host\0")
        body = document(b"\x10status\0" + struct.pack("<i", 0) + b"\x04routes\0" + routes)
        self.assertEqual(probe.decode_bson(body), {"status": 0, "routes": ["host"]})

    def test_rejects_malformed_bson_before_using_it(self):
        cases = [
            b"", struct.pack("<i", -1) + b"\0", document(b"") + b"extra",
            document(b"\x02x\0" + struct.pack("<i", 0)),
            document(b"\x02x\0" + struct.pack("<i", 2) + b"xy"),
            document(b"\x08x\0\x02"), document(b"\x12x\0short"),
            document(b"\x0ax\0\x0ax\0"), document(b"\x04x\0" + document(b"\x0a1\0")),
        ]
        for value in cases:
            with self.subTest(value=value), self.assertRaises(ValueError):
                probe.decode_bson(value)

    def test_rejects_excessive_nesting(self):
        body = document(b"")
        for _ in range(18):
            body = document(b"\x03nested\0" + body)
        with self.assertRaises(ValueError):
            probe.decode_bson(body)

    def test_checks_packet_size_and_command_before_reading_body(self):
        for method, size in [(b"GETCONF", probe.MAX_BODY + 1), (b"WRITE", 5), (b"GETCONF", 0)]:
            connection = Mock()
            connection.recv.return_value = probe.HEADER.pack(1, 0, method.ljust(11, b"\0"), 0, size)
            with self.subTest(method=method, size=size), self.assertRaises(ValueError):
                probe.receive_booking(connection, time.monotonic() + 1)
            connection.recv.assert_called_once_with(22)

    def test_incomplete_response_and_deadline_fail(self):
        connection = Mock()
        connection.recv.side_effect = [b"ab", b""]
        with self.assertRaises(EOFError):
            probe.receive_exact(connection, 4, time.monotonic() + 1)
        with self.assertRaises(TimeoutError):
            probe.receive_exact(connection, 4, time.monotonic() - 1)


if __name__ == "__main__":
    unittest.main()
