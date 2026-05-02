import argparse
import builtins
import importlib
import io
import os
import socket
import sys
import tempfile
import unittest
import zipfile
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
    def test_parse_args_legacy_defaults_to_netloader(self):
        args = three_dslink.parse_args(["sample-app.3dsx"])

        self.assertEqual(args.command, three_dslink.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dslink.LOAD_ACTION)
        self.assertTrue(args.legacy)
        self.assertEqual(args.file, "sample-app.3dsx")
        self.assertIsNone(args.host)
        self.assertEqual(args.port, three_dslink.DEFAULT_NETLOADER_PORT)

    def test_parse_args_accepts_explicit_netloader(self):
        args = three_dslink.parse_args(["netloader", "--host", "192.168.0.10", "--port", "1234", "sample-app.3dsx"])

        self.assertEqual(args.command, three_dslink.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dslink.LOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 1234)

    def test_parse_args_accepts_explicit_netloader_load(self):
        args = three_dslink.parse_args(["netloader", "load", "--host", "192.168.0.10", "sample-app.3dsx"])

        self.assertEqual(args.command, three_dslink.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dslink.LOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.file, "sample-app.3dsx")

    def test_parse_args_accepts_explicit_ftp_defaults(self):
        args = three_dslink.parse_args([
            "ftp",
            "--source", "sample-app.3dsx",
            "--dest", "/3ds/",
            "--unarchive",
            "--patterns", "*.nds",
            "--patterns", "*.gba",
            "--source", "extra.gbc",
        ])

        self.assertEqual(args.command, three_dslink.FTP_COMMAND)
        self.assertEqual(args.action, three_dslink.UPLOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.source, ["sample-app.3dsx", "extra.gbc"])
        self.assertEqual(args.dest, "/3ds/")
        self.assertEqual(args.port, three_dslink.DEFAULT_FTP_PORT)
        self.assertEqual(args.user, "anonymous")
        self.assertEqual(args.password, "")
        self.assertTrue(args.unarchive)
        self.assertEqual(args.patterns, ["*.nds", "*.gba"])

    def test_parse_args_accepts_explicit_ftp_upload(self):
        args = three_dslink.parse_args([
            "ftp", "upload", "--host", "192.168.0.10", "--source", "sample-app.3dsx", "--dest", "/3ds/"
        ])

        self.assertEqual(args.command, three_dslink.FTP_COMMAND)
        self.assertEqual(args.action, three_dslink.UPLOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.source, ["sample-app.3dsx"])
        self.assertEqual(args.dest, "/3ds/")

    def test_parse_args_rejects_invalid_netloader_port(self):
        with self.assertRaises(SystemExit):
            three_dslink.parse_args(["netloader", "--port", "70000", "sample-app.3dsx"])

    def test_parse_args_rejects_invalid_ftp_port(self):
        with self.assertRaises(SystemExit):
            three_dslink.parse_args(["ftp", "--port", "70000", "--source", "sample-app.3dsx", "--dest", "/3ds/"])

    def test_parse_args_accepts_explicit_netloader_status(self):
        args = three_dslink.parse_args(["netloader", "status", "--host", "192.168.0.10", "--port", "1234"])

        self.assertEqual(args.command, three_dslink.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dslink.STATUS_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 1234)

    def test_parse_args_accepts_explicit_ftp_status(self):
        args = three_dslink.parse_args(["ftp", "status", "--host", "192.168.0.10", "--user", "user", "--password", "secret"])

        self.assertEqual(args.command, three_dslink.FTP_COMMAND)
        self.assertEqual(args.action, three_dslink.STATUS_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, three_dslink.DEFAULT_FTP_PORT)
        self.assertEqual(args.user, "user")
        self.assertEqual(args.password, "secret")

    def test_parse_args_accepts_default_status_as_netloader(self):
        args = three_dslink.parse_args(["status", "--host", "192.168.0.10"])

        self.assertEqual(args.command, three_dslink.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dslink.STATUS_ACTION)
        self.assertTrue(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")

    def test_parse_args_rejects_invalid_status_port(self):
        with self.assertRaises(SystemExit):
            three_dslink.parse_args(["netloader", "status", "--port", "70000"])


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


class ValidateNetloaderFileTests(unittest.TestCase):
    def test_validate_netloader_file_accepts_3dsx_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".3dsx") as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            three_dslink.validate_netloader_file(file_obj.name)

    def test_validate_netloader_file_accepts_3dsx_magic_without_extension(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"3DSX payload")
            file_obj.flush()

            three_dslink.validate_netloader_file(file_obj.name)

    def test_validate_netloader_file_rejects_non_3dsx_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bin") as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            with self.assertRaises(three_dslink.NetloaderError):
                three_dslink.validate_netloader_file(file_obj.name)


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


class FTPTransferTests(unittest.TestCase):
    def make_payload_file(self):
        file_obj = tempfile.NamedTemporaryFile()
        file_obj.write(b"payload")
        file_obj.flush()
        return file_obj

    def test_send_ftp_uploads_basename_to_root_anonymously(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            three_dslink.send_ftp("192.168.0.10", 5000, file_obj.name, "/", "anonymous", "")

        ftp.connect.assert_called_once_with("192.168.0.10", 5000, timeout=three_dslink.DEFAULT_TIMEOUT)
        ftp.set_pasv.assert_called_once_with(True)
        ftp.login.assert_called_once_with(user="anonymous", passwd="")
        ftp.storbinary.assert_called_once()
        self.assertEqual(ftp.storbinary.call_args.args[0], f"STOR /{os.path.basename(file_obj.name)}")

    def test_send_ftp_honors_credentials_remote_and_port(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dslink.ftplib.error_perm("550 not dir")
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            three_dslink.send_ftp("192.168.0.10", 2121, file_obj.name, "/3ds/app.3dsx", "user", "secret")

        ftp.connect.assert_called_once_with("192.168.0.10", 2121, timeout=three_dslink.DEFAULT_TIMEOUT)
        ftp.login.assert_called_once_with(user="user", passwd="secret")
        self.assertEqual(ftp.storbinary.call_args.args[0], "STOR /3ds/app.3dsx")

    def test_send_ftp_wraps_ftp_errors(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.storbinary.side_effect = three_dslink.ftplib.error_perm("550 failed")
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp), \
            self.assertRaises(three_dslink.FTPTransferError):
            three_dslink.send_ftp("192.168.0.10", 5000, file_obj.name, "/", "anonymous", "")

    def test_send_ftp_skips_file_when_remote_size_matches(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dslink.ftplib.error_perm("550 not dir")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            ftp.size.return_value = os.path.getsize(file_obj.name)
            three_dslink.send_ftp("192.168.0.10", 5000, file_obj.name, "/remote.bin", "anonymous", "")

        ftp.storbinary.assert_not_called()

    def test_send_ftp_renames_file_when_remote_size_differs(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dslink.ftplib.error_perm("550 not dir")
        ftp.size.side_effect = [999, three_dslink.ftplib.error_perm("550 missing")]

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            three_dslink.send_ftp("192.168.0.10", 5000, file_obj.name, "/remote.bin", "anonymous", "")

        self.assertEqual(ftp.storbinary.call_args.args[0], "STOR /remote_1.bin")

    def test_send_ftp_uploads_directory_contents_to_dest_directory(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            os.mkdir(os.path.join(source_dir, "nested"))
            first = os.path.join(source_dir, "first.bin")
            second = os.path.join(source_dir, "nested", "second.bin")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dslink.send_ftp("192.168.0.10", 5000, source_dir, "/dest", "anonymous", "")

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.bin", "STOR /dest/nested/second.bin"])

    def test_send_ftp_uploads_multiple_sources_to_dest_directory(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            first = os.path.join(source_dir, "first.nds")
            second = os.path.join(source_dir, "second.gba")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dslink.send_ftp("192.168.0.10", 5000, [first, second], "/dest", "anonymous", "")

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.nds", "STOR /dest/second.gba"])

    def test_send_ftp_filters_multiple_sources(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            first = os.path.join(source_dir, "first.nds")
            second = os.path.join(source_dir, "second.gba")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dslink.send_ftp("192.168.0.10", 5000, [first, second], "/dest", "anonymous", "", patterns=["*.nds"])

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.nds"])

    def test_send_ftp_filters_directory_contents(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dslink.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            os.mkdir(os.path.join(source_dir, "nested"))
            keep = os.path.join(source_dir, "nested", "keep.nds")
            skip = os.path.join(source_dir, "skip.txt")
            with open(keep, "wb") as file_obj:
                file_obj.write(b"keep")
            with open(skip, "wb") as file_obj:
                file_obj.write(b"skip")

            three_dslink.send_ftp(
                "192.168.0.10",
                5000,
                source_dir,
                "/dest",
                "anonymous",
                "",
                patterns=["*.nds"],
            )

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/nested/keep.nds"])

    def test_iter_ftp_sources_skips_archives_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = os.path.join(temp_dir, "source")
            os.mkdir(source_dir)
            archive_path = os.path.join(source_dir, "roms.zip")
            game_path = os.path.join(source_dir, "game.nds")
            with open(archive_path, "wb") as file_obj:
                file_obj.write(b"archive")
            with open(game_path, "wb") as file_obj:
                file_obj.write(b"game")

            result = list(three_dslink.iter_ftp_sources(source_dir, skip_archives=True))

        self.assertEqual(result, [(game_path, "game.nds")])

    def test_iter_ftp_sources_skips_archive_file_source_when_requested(self):
        with tempfile.NamedTemporaryFile(suffix=".zip") as file_obj:
            result = list(three_dslink.iter_ftp_sources(file_obj.name, skip_archives=True))

        self.assertEqual(result, [])

    def test_unarchive_ftp_source_extracts_zip_to_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "payload.zip")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(extract_dir)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("games/demo.nds", b"payload")

            result = three_dslink.unarchive_ftp_source(archive_path, extract_dir)

            self.assertEqual(result, extract_dir)
            self.assertTrue(os.path.exists(os.path.join(extract_dir, "games", "demo.nds")))

    def test_unarchive_ftp_sources_extracts_archives_from_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = os.path.join(temp_dir, "source")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(source_dir)
            os.mkdir(extract_dir)
            os.mkdir(os.path.join(source_dir, "nested"))
            first_archive = os.path.join(source_dir, "first.zip")
            second_archive = os.path.join(source_dir, "nested", "second.zip")
            with zipfile.ZipFile(first_archive, "w") as archive:
                archive.writestr("first.nds", b"first")
            with zipfile.ZipFile(second_archive, "w") as archive:
                archive.writestr("second.gba", b"second")

            result = three_dslink.unarchive_ftp_sources(source_dir, extract_dir)

            extracted = []
            for root, _, files in os.walk(result):
                for filename in files:
                    extracted.append(os.path.relpath(os.path.join(root, filename), result).replace(os.sep, "/"))

            self.assertEqual(
                sorted(extracted),
                [
                    "first.nds",
                    "second.gba",
                ],
            )

    def test_unarchive_ftp_sources_rejects_directory_without_archives(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_dir = os.path.join(temp_dir, "source")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(source_dir)
            os.mkdir(extract_dir)
            with open(os.path.join(source_dir, "readme.txt"), "w") as file_obj:
                file_obj.write("not an archive")

            with self.assertRaises(three_dslink.FTPTransferError):
                three_dslink.unarchive_ftp_sources(source_dir, extract_dir)

    def test_should_unarchive_ftp_prompts_yes_by_default_when_archives_present(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dslink, "has_archive_sources", return_value=True), \
            mock.patch.object(builtins, "input", return_value=""):
            self.assertTrue(three_dslink.should_unarchive_ftp(args, stdin=stdin))

    def test_should_unarchive_ftp_prompts_no_when_user_declines(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dslink, "has_archive_sources", return_value=True), \
            mock.patch.object(builtins, "input", return_value="n"):
            self.assertFalse(three_dslink.should_unarchive_ftp(args, stdin=stdin))

    def test_should_unarchive_ftp_skips_prompt_when_noninteractive(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)

        with mock.patch.object(three_dslink, "has_archive_sources") as has_archive_mock:
            self.assertFalse(three_dslink.should_unarchive_ftp(args, stdin=io.StringIO("")))

        has_archive_mock.assert_not_called()

    def test_should_unarchive_ftp_skips_prompt_when_no_archives_present(self):
        args = argparse.Namespace(source=["file.nds"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dslink, "has_archive_sources", return_value=False), \
            mock.patch.object(builtins, "input") as input_mock:
            self.assertFalse(three_dslink.should_unarchive_ftp(args, stdin=stdin))

        input_mock.assert_not_called()

    def test_has_archive_sources_ignores_non_archive_file_sources(self):
        with tempfile.NamedTemporaryFile(suffix=".nds") as file_obj:
            self.assertFalse(three_dslink.has_archive_sources([file_obj.name]))

    def test_unarchive_ftp_source_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "payload.zip")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(extract_dir)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.nds", b"payload")

            with self.assertRaises(three_dslink.FTPTransferError):
                three_dslink.unarchive_ftp_source(archive_path, extract_dir)


class FTPHostResolutionTests(unittest.TestCase):
    def test_resolve_ftp_host_uses_single_mdns_candidate(self):
        with mock.patch.object(three_dslink, "discover_ftp_mdns", return_value=[("n3ds.local", 5000)]), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.55") as resolve_mock:
            host, port = three_dslink.resolve_ftp_host(None, 5000, stdin=io.StringIO(""))

        self.assertEqual((host, port), ("192.168.0.55", 5000))
        resolve_mock.assert_called_once_with("n3ds.local", 5000)

    def test_resolve_ftp_host_prompts_when_interactive_and_no_candidate(self):
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dslink, "discover_ftp_mdns", return_value=[]), \
            mock.patch.object(builtins, "input", return_value="192.168.0.60:6000"), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.60"):
            host, port = three_dslink.resolve_ftp_host(None, 5000, stdin=stdin)

        self.assertEqual((host, port), ("192.168.0.60", 6000))

    def test_resolve_ftp_host_errors_when_noninteractive_and_no_candidate(self):
        with mock.patch.object(three_dslink, "discover_ftp_mdns", return_value=[]):
            with self.assertRaises(three_dslink.DiscoveryError):
                three_dslink.resolve_ftp_host(None, 5000, stdin=io.StringIO(""))


class StatusTests(unittest.TestCase):
    def test_check_tcp_status_connects_to_host_and_port(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False

        with mock.patch.object(three_dslink.socket, "create_connection", return_value=sock) as connect_mock, \
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            three_dslink.check_tcp_status("192.168.0.10", 17491, "NetLoader")

        connect_mock.assert_called_once_with(("192.168.0.10", 17491), timeout=three_dslink.DEFAULT_TIMEOUT)
        sock.settimeout.assert_called_once_with(three_dslink.DEFAULT_TIMEOUT)
        self.assertIn("NetLoader is reachable at 192.168.0.10:17491", stdout.getvalue())
        self.assertIn("Restart it before loading a .3dsx file", stdout.getvalue())

    def test_check_tcp_status_wraps_socket_errors(self):
        with mock.patch.object(three_dslink.socket, "create_connection", side_effect=OSError("refused")):
            with self.assertRaises(three_dslink.NetloaderError):
                three_dslink.check_tcp_status("192.168.0.10", 17491, "NetLoader")

    def test_check_ftp_status_connects_and_logs_in(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False

        with mock.patch.object(three_dslink.ftplib, "FTP", return_value=ftp):
            three_dslink.check_ftp_status("192.168.0.10", 5000, "user", "secret")

        ftp.connect.assert_called_once_with("192.168.0.10", 5000, timeout=three_dslink.DEFAULT_TIMEOUT)
        ftp.set_pasv.assert_called_once_with(True)
        ftp.login.assert_called_once_with(user="user", passwd="secret")

    def test_run_status_uses_netloader_status(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, host="3ds.local", port=17491)

        with mock.patch.object(three_dslink, "resolve_netloader_host", return_value=("192.168.0.10", 17491)) as resolve_mock, \
            mock.patch.object(three_dslink, "check_tcp_status") as status_mock:
            three_dslink.run_status(args)

        resolve_mock.assert_called_once_with(args)
        status_mock.assert_called_once_with("192.168.0.10", 17491, "NetLoader")

    def test_run_status_uses_ftp_status(self):
        args = argparse.Namespace(
            command=three_dslink.FTP_COMMAND,
            host="3ds.local",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dslink, "resolve_ftp_host", return_value=("192.168.0.10", 5000)) as resolve_mock, \
            mock.patch.object(three_dslink, "check_ftp_status") as status_mock:
            three_dslink.run_status(args)

        resolve_mock.assert_called_once_with("3ds.local", 5000)
        status_mock.assert_called_once_with("192.168.0.10", 5000, "anonymous", "")


class MainTests(unittest.TestCase):
    def test_main_discovers_host_then_sends_file(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "discover_3ds", return_value="192.168.0.44") as discover_mock, \
            mock.patch.object(three_dslink, "send_3dsx") as send_mock:
            result = three_dslink.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        discover_mock.assert_called_once_with(17491, three_dslink.DEFAULT_DISCOVERY_RETRIES, 1.0)
        send_mock.assert_called_once_with(host="192.168.0.44", port=17491, path="sample-app.3dsx")

    def test_main_prompts_for_netloader_host_when_discovery_fails_interactively(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "discover_3ds", side_effect=three_dslink.DiscoveryError("no reply")), \
            mock.patch.object(three_dslink.sys, "stdin", stdin), \
            mock.patch.object(builtins, "input", return_value="3ds.local:1234"), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.99") as resolve_mock, \
            mock.patch.object(three_dslink, "send_3dsx") as send_mock:
            result = three_dslink.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        resolve_mock.assert_called_once_with("3ds.local", 1234)
        send_mock.assert_called_once_with(host="192.168.0.99", port=1234, path="sample-app.3dsx")

    def test_main_keeps_netloader_discovery_error_when_noninteractive(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "discover_3ds", side_effect=three_dslink.DiscoveryError("no reply")), \
            mock.patch.object(three_dslink.sys, "stdin", io.StringIO("")):
            result = three_dslink.main(["sample-app.3dsx"])

        self.assertEqual(result, 1)

    def test_main_uses_explicit_host_without_discovery(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host="3ds.local", port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.99") as resolve_mock, \
            mock.patch.object(three_dslink, "send_3dsx") as send_mock:
            result = three_dslink.main(["--host", "3ds.local", "sample-app.3dsx"])

        self.assertEqual(result, 0)
        resolve_mock.assert_called_once_with("3ds.local", 17491)
        send_mock.assert_called_once_with(host="192.168.0.99", port=17491, path="sample-app.3dsx")

    def test_main_returns_1_on_netloader_error(self):
        args = argparse.Namespace(command=three_dslink.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host="192.168.0.10", port=17491)

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "resolve_host", return_value="192.168.0.10"), \
            mock.patch.object(three_dslink, "send_3dsx", side_effect=three_dslink.NetloaderError("boom")):
            result = three_dslink.main(["--host", "192.168.0.10", "sample-app.3dsx"])

        self.assertEqual(result, 1)

    def test_main_warns_for_legacy_form(self):
        with mock.patch.object(three_dslink, "run_netloader") as run_mock, \
            mock.patch.object(sys, "stderr", new_callable=io.StringIO) as stderr:
            result = three_dslink.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once()
        self.assertIn("deprecated", stderr.getvalue())

    def test_main_runs_default_status_without_legacy_warning(self):
        with mock.patch.object(three_dslink, "run_status") as run_mock, \
            mock.patch.object(sys, "stderr", new_callable=io.StringIO) as stderr:
            result = three_dslink.main(["status", "--host", "192.168.0.10"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once()
        self.assertNotIn("deprecated", stderr.getvalue())

    def test_main_runs_ftp_command(self):
        args = argparse.Namespace(
            command=three_dslink.FTP_COMMAND,
            legacy=False,
            source=["sample-app.3dsx"],
            dest="/3ds/",
            unarchive=False,
            patterns=None,
            host="192.168.0.10",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dslink, "parse_args", return_value=args), \
            mock.patch.object(three_dslink, "run_ftp") as run_mock:
            result = three_dslink.main(["ftp", "--host", "192.168.0.10", "--source", "sample-app.3dsx", "--dest", "/3ds/"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once_with(args)

    def test_run_ftp_unarchives_before_uploading(self):
        args = argparse.Namespace(
            source=["archive.zip"],
            dest="/3ds/",
            unarchive=True,
            patterns=["*.nds"],
            host="192.168.0.10",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dslink, "resolve_ftp_host", return_value=("192.168.0.10", 5000)), \
            mock.patch.object(three_dslink, "get_ftp_archive_action", return_value=three_dslink.FTP_ARCHIVE_UNARCHIVE), \
            mock.patch.object(three_dslink, "unarchive_ftp_sources", return_value="/tmp/extracted") as unarchive_mock, \
            mock.patch.object(three_dslink, "send_ftp") as send_mock:
            three_dslink.run_ftp(args)

        unarchive_mock.assert_called_once()
        self.assertEqual(unarchive_mock.call_args.args[0], ["archive.zip"])
        send_mock.assert_called_once_with(
            host="192.168.0.10",
            port=5000,
            source="/tmp/extracted",
            dest="/3ds/",
            user="anonymous",
            password="",
            patterns=["*.nds"],
        )

    def test_run_ftp_skips_archives_when_prompt_is_declined(self):
        args = argparse.Namespace(
            source=["archive.zip", "game.nds"],
            dest="/roms/",
            unarchive=False,
            patterns=None,
            host="192.168.0.10",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dslink, "resolve_ftp_host", return_value=("192.168.0.10", 5000)), \
            mock.patch.object(three_dslink, "get_ftp_archive_action", return_value=three_dslink.FTP_ARCHIVE_SKIP), \
            mock.patch.object(three_dslink, "send_ftp") as send_mock:
            three_dslink.run_ftp(args)

        send_mock.assert_called_once_with(
            host="192.168.0.10",
            port=5000,
            source=["archive.zip", "game.nds"],
            dest="/roms/",
            user="anonymous",
            password="",
            patterns=None,
            skip_archives=True,
        )


if __name__ == "__main__":
    unittest.main()
