import argparse
import importlib
import io
import socket
import tempfile
import unittest
import zlib
from unittest import mock

three_dslink = importlib.import_module("3dslink")


class BuildCommandBufferTests(unittest.TestCase):
    def test_build_command_buffer_is_null_terminated(self):
        result = three_dslink.build_command_buffer("sdmc:/3ds/sample-app.3dsx")

        self.assertEqual(result, b"sdmc:/3ds/sample-app.3dsx\0")


class CompressionTests(unittest.TestCase):
    def test_iter_compressed_chunks_round_trips_original_data(self):
        original = (b"3dslink test payload" * 1024) + b"!"
        file_obj = io.BytesIO(original)

        chunks = list(three_dslink.iter_compressed_chunks(file_obj))
        restored = zlib.decompress(b"".join(chunks))

        self.assertGreater(len(chunks), 0)
        self.assertEqual(restored, original)


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_defaults(self):
        args = three_dslink.parse_args(["sample-app.3dsx"])

        self.assertEqual(args.file, "sample-app.3dsx")
        self.assertIsNone(args.host)
        self.assertEqual(args.port, three_dslink.DEFAULT_PORT)

    def test_parse_args_accepts_host_and_port(self):
        args = three_dslink.parse_args(["--host", "192.168.0.10", "--port", "1234", "sample-app.3dsx"])

        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 1234)

    def test_parse_args_rejects_invalid_port(self):
        with self.assertRaises(SystemExit):
            three_dslink.parse_args(["--port", "70000", "sample-app.3dsx"])


class ValidateInputFileTests(unittest.TestCase):
    def test_validate_input_file_rejects_missing_path(self):
        with self.assertRaises(three_dslink.NetloaderError):
            three_dslink.validate_input_file("missing.3dsx")

    def test_validate_input_file_rejects_empty_file(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            with self.assertRaises(three_dslink.NetloaderError):
                three_dslink.validate_input_file(file_obj.name)

    def test_validate_input_file_accepts_non_empty_file(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            three_dslink.validate_input_file(file_obj.name)


class Discover3DSTests(unittest.TestCase):
    def make_context_socket(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False
        return sock

    def test_discover_3ds_returns_remote_ip_when_boot_reply_arrives(self):
        recv_sock = self.make_context_socket()
        send_sock = self.make_context_socket()
        recv_sock.recvfrom.side_effect = [
            (three_dslink.DISCOVERY_RESPONSE_PREFIX + b"-reply", ("192.168.0.55", 17491)),
        ]

        with mock.patch.object(three_dslink.socket, "socket", side_effect=[recv_sock, send_sock]):
            host = three_dslink.discover_3ds(17491, retries=1, attempt_interval=0.1)

        self.assertEqual(host, "192.168.0.55")
        send_sock.sendto.assert_called_once_with(three_dslink.DISCOVERY_REQUEST, ("255.255.255.255", 17491))

    def test_discover_3ds_raises_when_no_console_replies(self):
        recv_sock = self.make_context_socket()
        send_sock = self.make_context_socket()
        recv_sock.recvfrom.side_effect = socket.timeout

        with mock.patch.object(three_dslink.socket, "socket", side_effect=[recv_sock, send_sock]):
            with self.assertRaises(three_dslink.DiscoveryError):
                three_dslink.discover_3ds(17491, retries=2, attempt_interval=0.1)


class MainTests(unittest.TestCase):
    def test_main_discovers_host_then_sends_file(self):
        args = argparse.Namespace(file="sample-app.3dsx", host=None, port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "discover_3ds", return_value="192.168.0.44") as discover_mock, \
            mock.patch.object(three_dslink, "send_3dsx") as send_mock:
            result = three_dslink.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        discover_mock.assert_called_once_with(17491, three_dslink.DEFAULT_DISCOVERY_RETRIES, 1.0)
        send_mock.assert_called_once_with(host="192.168.0.44", port=17491, path="sample-app.3dsx")

    def test_main_uses_explicit_host_without_discovery(self):
        args = argparse.Namespace(file="sample-app.3dsx", host="3ds.local", port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.99") as resolve_mock, \
            mock.patch.object(three_dslink, "send_3dsx") as send_mock:
            result = three_dslink.main(["--host", "3ds.local", "sample-app.3dsx"])

        self.assertEqual(result, 0)
        resolve_mock.assert_called_once_with("3ds.local", 17491)
        send_mock.assert_called_once_with(host="192.168.0.99", port=17491, path="sample-app.3dsx")

    def test_main_returns_1_on_netloader_error(self):
        args = argparse.Namespace(file="sample-app.3dsx", host="192.168.0.10", port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.10"), \
            mock.patch.object(three_dslink, "send_3dsx", side_effect=three_dslink.NetloaderError("boom")):
            result = three_dslink.main(["--host", "192.168.0.10", "sample-app.3dsx"])

        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
