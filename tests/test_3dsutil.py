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

import ftp as ftp_module

three_dsutil = importlib.import_module("3dsutil")


class BuildCommandBufferTests(unittest.TestCase):
    def test_build_command_buffer_is_null_terminated(self):
        result = three_dsutil.build_command_buffer("sdmc:/3ds/sample-app.3dsx")

        self.assertEqual(result, b"sdmc:/3ds/sample-app.3dsx\0")


class CompressionTests(unittest.TestCase):
    def test_iter_compressed_chunks_round_trips_original_data(self):
        original = (b"3dsutil test payload" * 1024) + b"!"
        file_obj = io.BytesIO(original)

        chunks = list(three_dsutil.iter_compressed_chunks(file_obj))
        restored = zlib.decompress(b"".join(chunks))

        self.assertGreater(len(chunks), 0)
        self.assertEqual(restored, original)


class ParseArgsTests(unittest.TestCase):
    def test_parse_args_legacy_defaults_to_netloader(self):
        args = three_dsutil.parse_args(["sample-app.3dsx"])

        self.assertEqual(args.command, three_dsutil.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dsutil.LOAD_ACTION)
        self.assertTrue(args.legacy)
        self.assertEqual(args.file, "sample-app.3dsx")
        self.assertIsNone(args.host)
        self.assertEqual(args.port, three_dsutil.DEFAULT_NETLOADER_PORT)

    def test_parse_args_accepts_explicit_netloader(self):
        args = three_dsutil.parse_args(["netloader", "--host", "192.168.0.10", "--port", "1234", "sample-app.3dsx"])

        self.assertEqual(args.command, three_dsutil.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dsutil.LOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 1234)

    def test_parse_args_accepts_explicit_netloader_load(self):
        args = three_dsutil.parse_args(["netloader", "load", "--host", "192.168.0.10", "sample-app.3dsx"])

        self.assertEqual(args.command, three_dsutil.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dsutil.LOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.file, "sample-app.3dsx")

    def test_parse_args_accepts_explicit_ftp_defaults(self):
        args = three_dsutil.parse_args([
            "ftp", "upload",
            "--source", "sample-app.3dsx",
            "--dest", "/3ds/",
            "--unarchive",
            "--patterns", "*.nds",
            "--patterns", "*.gba",
            "--source", "extra.gbc",
        ])

        self.assertEqual(args.command, three_dsutil.FTP_COMMAND)
        self.assertEqual(args.action, three_dsutil.UPLOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.source, ["sample-app.3dsx", "extra.gbc"])
        self.assertEqual(args.dest, "/3ds/")
        self.assertEqual(args.port, three_dsutil.DEFAULT_FTP_PORT)
        self.assertEqual(args.user, "anonymous")
        self.assertEqual(args.password, "")
        self.assertTrue(args.unarchive)
        self.assertEqual(args.patterns, ["*.nds", "*.gba"])

    def test_parse_args_ftp_defaults_to_explorer(self):
        args = three_dsutil.parse_args(["ftp", "--host", "192.168.0.10", "--source", "~/Downloads", "--dest", "/3ds"])

        self.assertEqual(args.command, three_dsutil.FTP_COMMAND)
        self.assertEqual(args.action, three_dsutil.EXPLORER_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, three_dsutil.DEFAULT_FTP_PORT)
        self.assertEqual(args.user, "anonymous")
        self.assertEqual(args.password, "")
        self.assertEqual(args.source, "~/Downloads")
        self.assertEqual(args.dest, "/3ds")

    def test_parse_args_accepts_explicit_ftp_explorer(self):
        args = three_dsutil.parse_args([
            "ftp", "explorer",
            "--host", "192.168.0.10",
            "--port", "6000",
            "--source", "~/Downloads",
            "--dest", "/roms",
        ])

        self.assertEqual(args.command, three_dsutil.FTP_COMMAND)
        self.assertEqual(args.action, three_dsutil.EXPLORER_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 6000)
        self.assertEqual(args.source, "~/Downloads")
        self.assertEqual(args.dest, "/roms")

    def test_parse_args_accepts_explicit_ftp_upload(self):
        args = three_dsutil.parse_args([
            "ftp", "upload", "--host", "192.168.0.10", "--source", "sample-app.3dsx", "--dest", "/3ds/"
        ])

        self.assertEqual(args.command, three_dsutil.FTP_COMMAND)
        self.assertEqual(args.action, three_dsutil.UPLOAD_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.source, ["sample-app.3dsx"])
        self.assertEqual(args.dest, "/3ds/")

    def test_parse_args_rejects_invalid_netloader_port(self):
        with self.assertRaises(SystemExit):
            three_dsutil.parse_args(["netloader", "--port", "70000", "sample-app.3dsx"])

    def test_parse_args_rejects_invalid_ftp_port(self):
        with self.assertRaises(SystemExit):
            three_dsutil.parse_args(["ftp", "--port", "70000"])

    def test_parse_args_accepts_explicit_netloader_status(self):
        args = three_dsutil.parse_args(["netloader", "status", "--host", "192.168.0.10", "--port", "1234"])

        self.assertEqual(args.command, three_dsutil.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dsutil.STATUS_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, 1234)

    def test_parse_args_accepts_explicit_ftp_status(self):
        args = three_dsutil.parse_args(["ftp", "status", "--host", "192.168.0.10", "--user", "user", "--password", "secret"])

        self.assertEqual(args.command, three_dsutil.FTP_COMMAND)
        self.assertEqual(args.action, three_dsutil.STATUS_ACTION)
        self.assertFalse(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")
        self.assertEqual(args.port, three_dsutil.DEFAULT_FTP_PORT)
        self.assertEqual(args.user, "user")
        self.assertEqual(args.password, "secret")

    def test_parse_args_accepts_default_status_as_netloader(self):
        args = three_dsutil.parse_args(["status", "--host", "192.168.0.10"])

        self.assertEqual(args.command, three_dsutil.NETLOADER_COMMAND)
        self.assertEqual(args.action, three_dsutil.STATUS_ACTION)
        self.assertTrue(args.legacy)
        self.assertEqual(args.host, "192.168.0.10")

    def test_parse_args_accepts_install_command(self):
        args = three_dsutil.parse_args(["install", "--install-root", "/tmp/3dsutil", "--bin-dir", "/tmp/bin", "--ref", "1.1"])

        self.assertEqual(args.command, three_dsutil.INSTALL_COMMAND)
        self.assertFalse(args.legacy)
        self.assertEqual(args.install_root, "/tmp/3dsutil")
        self.assertEqual(args.bin_dir, "/tmp/bin")
        self.assertEqual(args.ref, "1.1")

    def test_parse_args_accepts_uninstall_command(self):
        args = three_dsutil.parse_args(["uninstall", "--install-root", "/tmp/3dsutil", "--bin-dir", "/tmp/bin"])

        self.assertEqual(args.command, three_dsutil.UNINSTALL_COMMAND)
        self.assertFalse(args.legacy)
        self.assertEqual(args.install_root, "/tmp/3dsutil")
        self.assertEqual(args.bin_dir, "/tmp/bin")

    def test_parse_args_accepts_update_command(self):
        args = three_dsutil.parse_args(["update", "--install-root", "/tmp/3dsutil", "--bin-dir", "/tmp/bin"])

        self.assertEqual(args.command, three_dsutil.UPDATE_COMMAND)
        self.assertFalse(args.legacy)
        self.assertEqual(args.install_root, "/tmp/3dsutil")
        self.assertEqual(args.bin_dir, "/tmp/bin")

    def test_parse_args_rejects_invalid_status_port(self):
        with self.assertRaises(SystemExit):
            three_dsutil.parse_args(["netloader", "status", "--port", "70000"])


class ValidateInputFileTests(unittest.TestCase):
    def test_validate_input_file_rejects_missing_path(self):
        with self.assertRaises(three_dsutil.NetloaderError):
            three_dsutil.validate_input_file("missing.3dsx")

    def test_validate_input_file_rejects_empty_file(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            with self.assertRaises(three_dsutil.NetloaderError):
                three_dsutil.validate_input_file(file_obj.name)

    def test_validate_input_file_accepts_non_empty_file(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            three_dsutil.validate_input_file(file_obj.name)


class ValidateNetloaderFileTests(unittest.TestCase):
    def test_validate_netloader_file_accepts_3dsx_extension(self):
        with tempfile.NamedTemporaryFile(suffix=".3dsx") as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            three_dsutil.validate_netloader_file(file_obj.name)

    def test_validate_netloader_file_accepts_3dsx_magic_without_extension(self):
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"3DSX payload")
            file_obj.flush()

            three_dsutil.validate_netloader_file(file_obj.name)

    def test_validate_netloader_file_rejects_non_3dsx_file(self):
        with tempfile.NamedTemporaryFile(suffix=".bin") as file_obj:
            file_obj.write(b"payload")
            file_obj.flush()

            with self.assertRaises(three_dsutil.NetloaderError):
                three_dsutil.validate_netloader_file(file_obj.name)


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
            (three_dsutil.DISCOVERY_RESPONSE_PREFIX + b"-reply", ("192.168.0.55", 17491)),
        ]

        with mock.patch.object(three_dsutil.socket, "socket", side_effect=[recv_sock, send_sock]):
            host = three_dsutil.discover_3ds(17491, retries=1, attempt_interval=0.1)

        self.assertEqual(host, "192.168.0.55")
        send_sock.sendto.assert_called_once_with(three_dsutil.DISCOVERY_REQUEST, ("255.255.255.255", 17491))

    def test_discover_3ds_raises_when_no_console_replies(self):
        recv_sock = self.make_context_socket()
        send_sock = self.make_context_socket()
        recv_sock.recvfrom.side_effect = socket.timeout

        with mock.patch.object(three_dsutil.socket, "socket", side_effect=[recv_sock, send_sock]):
            with self.assertRaises(three_dsutil.DiscoveryError):
                three_dsutil.discover_3ds(17491, retries=2, attempt_interval=0.1)


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
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            three_dsutil.send_ftp("192.168.0.10", 5000, file_obj.name, "/", "anonymous", "")

        ftp.connect.assert_called_once_with("192.168.0.10", 5000, timeout=three_dsutil.DEFAULT_TIMEOUT)
        ftp.set_pasv.assert_called_once_with(True)
        ftp.login.assert_called_once_with(user="anonymous", passwd="")
        ftp.storbinary.assert_called_once()
        self.assertEqual(ftp.storbinary.call_args.args[0], f"STOR /{os.path.basename(file_obj.name)}")

    def test_send_ftp_honors_credentials_remote_and_port(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dsutil.ftplib.error_perm("550 not dir")
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            three_dsutil.send_ftp("192.168.0.10", 2121, file_obj.name, "/3ds/app.3dsx", "user", "secret")

        ftp.connect.assert_called_once_with("192.168.0.10", 2121, timeout=three_dsutil.DEFAULT_TIMEOUT)
        ftp.login.assert_called_once_with(user="user", passwd="secret")
        self.assertEqual(ftp.storbinary.call_args.args[0], "STOR /3ds/app.3dsx")

    def test_send_ftp_wraps_ftp_errors(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.storbinary.side_effect = three_dsutil.ftplib.error_perm("550 failed")
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp), \
            self.assertRaises(three_dsutil.FTPTransferError):
            three_dsutil.send_ftp("192.168.0.10", 5000, file_obj.name, "/", "anonymous", "")

    def test_send_ftp_skips_file_when_remote_size_matches(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dsutil.ftplib.error_perm("550 not dir")

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            ftp.size.return_value = os.path.getsize(file_obj.name)
            three_dsutil.send_ftp("192.168.0.10", 5000, file_obj.name, "/remote.bin", "anonymous", "")

        ftp.storbinary.assert_not_called()

    def test_send_ftp_renames_file_when_remote_size_differs(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.cwd.side_effect = three_dsutil.ftplib.error_perm("550 not dir")
        ftp.size.side_effect = [999, three_dsutil.ftplib.error_perm("550 missing")]

        with self.make_payload_file() as file_obj, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            three_dsutil.send_ftp("192.168.0.10", 5000, file_obj.name, "/remote.bin", "anonymous", "")

        self.assertEqual(ftp.storbinary.call_args.args[0], "STOR /remote_1.bin")

    def test_send_ftp_uploads_directory_contents_to_dest_directory(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            os.mkdir(os.path.join(source_dir, "nested"))
            first = os.path.join(source_dir, "first.bin")
            second = os.path.join(source_dir, "nested", "second.bin")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dsutil.send_ftp("192.168.0.10", 5000, source_dir, "/dest", "anonymous", "")

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.bin", "STOR /dest/nested/second.bin"])

    def test_send_ftp_uploads_multiple_sources_to_dest_directory(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            first = os.path.join(source_dir, "first.nds")
            second = os.path.join(source_dir, "second.gba")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dsutil.send_ftp("192.168.0.10", 5000, [first, second], "/dest", "anonymous", "")

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.nds", "STOR /dest/second.gba"])

    def test_send_ftp_filters_multiple_sources(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            first = os.path.join(source_dir, "first.nds")
            second = os.path.join(source_dir, "second.gba")
            with open(first, "wb") as file_obj:
                file_obj.write(b"first")
            with open(second, "wb") as file_obj:
                file_obj.write(b"second")

            three_dsutil.send_ftp("192.168.0.10", 5000, [first, second], "/dest", "anonymous", "", patterns=["*.nds"])

        commands = [call.args[0] for call in ftp.storbinary.call_args_list]
        self.assertEqual(commands, ["STOR /dest/first.nds"])

    def test_send_ftp_filters_directory_contents(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False
        ftp.size.side_effect = three_dsutil.ftplib.error_perm("550 missing")

        with tempfile.TemporaryDirectory() as source_dir, \
            mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            os.mkdir(os.path.join(source_dir, "nested"))
            keep = os.path.join(source_dir, "nested", "keep.nds")
            skip = os.path.join(source_dir, "skip.txt")
            with open(keep, "wb") as file_obj:
                file_obj.write(b"keep")
            with open(skip, "wb") as file_obj:
                file_obj.write(b"skip")

            three_dsutil.send_ftp(
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

            result = list(three_dsutil.iter_ftp_sources(source_dir, skip_archives=True))

        self.assertEqual(result, [(game_path, "game.nds")])

    def test_iter_ftp_sources_skips_archive_file_source_when_requested(self):
        with tempfile.NamedTemporaryFile(suffix=".zip") as file_obj:
            result = list(three_dsutil.iter_ftp_sources(file_obj.name, skip_archives=True))

        self.assertEqual(result, [])

    def test_unarchive_ftp_source_extracts_zip_to_destination(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "payload.zip")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(extract_dir)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("games/demo.nds", b"payload")

            result = three_dsutil.unarchive_ftp_source(archive_path, extract_dir)

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

            result = three_dsutil.unarchive_ftp_sources(source_dir, extract_dir)

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

            with self.assertRaises(three_dsutil.FTPTransferError):
                three_dsutil.unarchive_ftp_sources(source_dir, extract_dir)

    def test_should_unarchive_ftp_prompts_yes_by_default_when_archives_present(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dsutil, "has_archive_sources", return_value=True), \
            mock.patch.object(builtins, "input", return_value=""):
            self.assertTrue(three_dsutil.should_unarchive_ftp(args, stdin=stdin))

    def test_should_unarchive_ftp_prompts_no_when_user_declines(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dsutil, "has_archive_sources", return_value=True), \
            mock.patch.object(builtins, "input", return_value="n"):
            self.assertFalse(three_dsutil.should_unarchive_ftp(args, stdin=stdin))

    def test_should_unarchive_ftp_skips_prompt_when_noninteractive(self):
        args = argparse.Namespace(source=["archive.zip"], unarchive=False)

        with mock.patch.object(three_dsutil, "has_archive_sources") as has_archive_mock:
            self.assertFalse(three_dsutil.should_unarchive_ftp(args, stdin=io.StringIO("")))

        has_archive_mock.assert_not_called()

    def test_should_unarchive_ftp_skips_prompt_when_no_archives_present(self):
        args = argparse.Namespace(source=["file.nds"], unarchive=False)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dsutil, "has_archive_sources", return_value=False), \
            mock.patch.object(builtins, "input") as input_mock:
            self.assertFalse(three_dsutil.should_unarchive_ftp(args, stdin=stdin))

        input_mock.assert_not_called()

    def test_has_archive_sources_ignores_non_archive_file_sources(self):
        with tempfile.NamedTemporaryFile(suffix=".nds") as file_obj:
            self.assertFalse(three_dsutil.has_archive_sources([file_obj.name]))

    def test_unarchive_ftp_source_rejects_zip_path_traversal(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            archive_path = os.path.join(temp_dir, "payload.zip")
            extract_dir = os.path.join(temp_dir, "extract")
            os.mkdir(extract_dir)
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("../escape.nds", b"payload")

            with self.assertRaises(three_dsutil.FTPTransferError):
                three_dsutil.unarchive_ftp_source(archive_path, extract_dir)

    def test_unarchive_ftp_source_prompts_to_install_7z_when_missing(self):
        with tempfile.NamedTemporaryFile(suffix=".7z") as file_obj, \
            tempfile.TemporaryDirectory() as extract_dir, \
            mock.patch.object(ftp_module, "find_7z_command", side_effect=[None, "/usr/bin/7z"]), \
            mock.patch.object(ftp_module, "prompt_install_command_dependency", return_value=True) as install_mock, \
            mock.patch.object(ftp_module.subprocess, "run") as run_mock:
            file_obj.write(b"payload")
            file_obj.flush()

            three_dsutil.unarchive_ftp_source(file_obj.name, extract_dir)

        install_mock.assert_called_once_with(
            "7z or 7zz",
            three_dsutil.SEVEN_ZIP_COMMANDS,
            {
                "apt-get": ("p7zip-full",),
                "dnf": ("p7zip",),
                "pacman": ("p7zip",),
                "brew": ("p7zip",),
            },
        )
        run_mock.assert_called_once()

    def test_prompt_install_command_dependency_returns_false_when_user_declines(self):
        with mock.patch.object(ftp_module, "command_exists", return_value=False), \
            mock.patch.object(ftp_module, "package_install_command", return_value="install p7zip"), \
            mock.patch.object(ftp_module, "ask_yes_no", return_value=False), \
            mock.patch.object(ftp_module.subprocess, "run") as run_mock:
            result = three_dsutil.prompt_install_command_dependency("7z", ("7z",), ("p7zip",))

        self.assertFalse(result)
        run_mock.assert_not_called()

    def test_list_ftp_directory_uses_mlsd_and_sorts_directories_first(self):
        ftp = mock.MagicMock()
        ftp.pwd.return_value = "/"
        ftp.mlsd.return_value = [
            ("game.nds", {"type": "file", "size": "1024", "modify": "20260502120000"}),
            ("3ds", {"type": "dir", "modify": "20260501120000"}),
        ]

        entries = three_dsutil.list_ftp_directory(ftp, "/")

        self.assertEqual([entry["name"] for entry in entries], ["..", "3ds", "game.nds"])
        self.assertEqual(entries[1]["type"], "dir")
        self.assertEqual(entries[2]["size"], "1024")
        ftp.cwd.assert_any_call("/")

    def test_format_size_uses_binary_units(self):
        self.assertEqual(three_dsutil.format_size("1536"), "1.5 KiB")

    def test_validate_local_explorer_dir_expands_home_and_requires_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            self.assertEqual(three_dsutil.validate_local_explorer_dir(temp_dir), os.path.abspath(temp_dir))

        with tempfile.NamedTemporaryFile() as file_obj:
            with self.assertRaises(three_dsutil.FTPTransferError):
                three_dsutil.validate_local_explorer_dir(file_obj.name)

    def test_list_local_directory_omits_parent_at_start_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with open(os.path.join(temp_dir, "game.nds"), "w") as file_obj:
                file_obj.write("game")

            entries = three_dsutil.list_local_directory(temp_dir, temp_dir)

        self.assertEqual([entry["name"] for entry in entries], ["game.nds"])

    def test_list_local_directory_includes_parent_below_start_dir(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            nested = os.path.join(temp_dir, "nested")
            os.mkdir(nested)
            with open(os.path.join(nested, "game.nds"), "w") as file_obj:
                file_obj.write("game")

            entries = three_dsutil.list_local_directory(nested, temp_dir)

        self.assertEqual([entry["name"] for entry in entries], ["..", "game.nds"])

    def test_join_local_explorer_path_goes_up_from_nested_dir(self):
        self.assertEqual(
            three_dsutil.join_local_explorer_path("/tmp/example/nested", ".."),
            "/tmp/example",
        )

    def test_join_local_explorer_path_stays_inside_start_dir(self):
        self.assertEqual(
            three_dsutil.join_local_explorer_path("/tmp/example", "..", "/tmp/example"),
            "/tmp/example",
        )

    def test_join_ftp_explorer_path_goes_up_from_nested_dir(self):
        self.assertEqual(three_dsutil.join_ftp_explorer_path("/roms/gba", ".."), "/roms")
        self.assertEqual(three_dsutil.join_ftp_explorer_path("/", ".."), "/")

    def test_ftp_entry_display_name_explains_parent_directory(self):
        self.assertEqual(
            three_dsutil.ftp_entry_display_name({"name": "..", "type": "dir"}),
            ".. (go up)",
        )
        self.assertEqual(
            three_dsutil.ftp_entry_display_name({"name": "roms", "type": "dir"}),
            "roms",
        )

    def test_move_ftp_selection_selects_previous_and_current_entries(self):
        entries = [
            {"name": "..", "type": "dir"},
            {"name": "first.nds", "type": "file"},
            {"name": "second.nds", "type": "file"},
        ]

        selected, selected_paths = three_dsutil.move_ftp_selection("/roms", entries, 1, set(), 1)

        self.assertEqual(selected, 2)
        self.assertEqual(selected_paths, {"/roms/first.nds", "/roms/second.nds"})

    def test_move_explorer_selection_tracks_side_with_paths(self):
        entries = [
            {"name": "..", "type": "dir"},
            {"name": "first.nds", "type": "file"},
            {"name": "second.nds", "type": "file"},
        ]

        selected, selected_paths = three_dsutil.move_explorer_selection("remote", "/roms", entries, 1, set(), 1)

        self.assertEqual(selected, 2)
        self.assertEqual(selected_paths, {("remote", "/roms/first.nds"), ("remote", "/roms/second.nds")})

    def test_restored_ftp_selection_selects_returned_directory(self):
        entries = [
            {"name": "..", "type": "dir"},
            {"name": "gba", "type": "dir"},
            {"name": "nds", "type": "dir"},
        ]

        self.assertEqual(three_dsutil.restored_ftp_selection(entries, "nds", 0), 2)
        self.assertEqual(three_dsutil.restored_ftp_selection(entries, "missing", 1), 1)

    def test_move_ftp_paths_renames_items_into_destination_directory(self):
        ftp = mock.MagicMock()
        items = [
            ("/roms/game.nds", {"name": "game.nds", "type": "file"}),
            ("/roms/saves", {"name": "saves", "type": "dir"}),
        ]

        moved = three_dsutil.move_ftp_paths(ftp, items, "/archive")

        self.assertEqual(
            moved,
            [
                ("/roms/game.nds", "/archive/game.nds"),
                ("/roms/saves", "/archive/saves"),
            ],
        )
        ftp.rename.assert_has_calls([
            mock.call("/roms/game.nds", "/archive/game.nds"),
            mock.call("/roms/saves", "/archive/saves"),
        ])

    def test_move_ftp_paths_rejects_moving_directory_into_itself(self):
        ftp = mock.MagicMock()

        with self.assertRaises(three_dsutil.FTPTransferError):
            three_dsutil.move_ftp_paths(
                ftp,
                [("/roms", {"name": "roms", "type": "dir"})],
                "/roms/nested",
            )

    def test_copy_or_move_explorer_items_dispatches_local_to_remote_copy(self):
        ftp = mock.MagicMock()
        item = ("local", "/tmp/game.nds", {"name": "game.nds", "type": "file"})

        with mock.patch.object(ftp_module, "copy_local_paths_to_remote", return_value=[("/tmp/game.nds", "/3ds/game.nds")]) as move_mock:
            moved = three_dsutil.copy_or_move_explorer_items(ftp, [item], "remote", "/3ds")

        self.assertEqual(moved, [("/tmp/game.nds", "/3ds/game.nds")])
        move_mock.assert_called_once_with(
            ftp,
            [("/tmp/game.nds", {"name": "game.nds", "type": "file"})],
            "/3ds",
            skip_archives=False,
            progress=None,
        )

    def test_copy_or_move_explorer_items_dispatches_remote_to_local_copy(self):
        ftp = mock.MagicMock()
        item = ("remote", "/3ds/game.nds", {"name": "game.nds", "type": "file"})

        with mock.patch.object(ftp_module, "copy_remote_paths_to_local", return_value=[("/3ds/game.nds", "/tmp/game.nds")]) as move_mock:
            moved = three_dsutil.copy_or_move_explorer_items(ftp, [item], "local", "/tmp")

        self.assertEqual(moved, [("/3ds/game.nds", "/tmp/game.nds")])
        move_mock.assert_called_once_with(
            ftp,
            [("/3ds/game.nds", {"name": "game.nds", "type": "file"})],
            "/tmp",
            progress=None,
        )

    def test_copy_local_paths_to_remote_skips_archives_inside_directories(self):
        ftp = mock.MagicMock()
        with tempfile.TemporaryDirectory() as source_dir:
            archive_path = os.path.join(source_dir, "roms.zip")
            keep_path = os.path.join(source_dir, "game.nds")
            with open(archive_path, "wb") as file_obj:
                file_obj.write(b"archive")
            with open(keep_path, "wb") as file_obj:
                file_obj.write(b"game")

            with mock.patch.object(ftp_module, "remote_size", return_value=None), \
                mock.patch.object(ftp_module, "upload_ftp_file") as upload_mock:
                three_dsutil.copy_local_paths_to_remote(
                    ftp,
                    [(source_dir, {"name": os.path.basename(source_dir), "type": "dir"})],
                    "/dest",
                    skip_archives=True,
                )

        uploaded_paths = [call.args[2] for call in upload_mock.call_args_list]
        self.assertEqual(uploaded_paths, ["/dest/" + os.path.basename(source_dir) + "/game.nds"])

    def test_upload_local_path_to_remote_skips_same_size_file(self):
        ftp = mock.MagicMock()
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"same")
            file_obj.flush()

            with mock.patch.object(ftp_module, "remote_size", return_value=os.path.getsize(file_obj.name)), \
                mock.patch.object(ftp_module, "upload_ftp_file") as upload_mock:
                destination = three_dsutil.upload_local_path_to_remote(ftp, file_obj.name, "/dest")

        self.assertIsNone(destination)
        upload_mock.assert_not_called()

    def test_upload_local_path_to_remote_renames_different_size_file(self):
        ftp = mock.MagicMock()
        with tempfile.NamedTemporaryFile() as file_obj:
            file_obj.write(b"new")
            file_obj.flush()

            with mock.patch.object(ftp_module, "remote_size", side_effect=[999, None]), \
                mock.patch.object(ftp_module, "upload_ftp_file") as upload_mock:
                destination = three_dsutil.upload_local_path_to_remote(ftp, file_obj.name, "/dest")

        self.assertTrue(destination.endswith("_1" + os.path.splitext(file_obj.name)[1]))
        upload_mock.assert_called_once()

    def test_download_remote_path_to_local_skips_same_size_file(self):
        ftp = mock.MagicMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = os.path.join(temp_dir, "game.nds")
            with open(destination, "wb") as file_obj:
                file_obj.write(b"same")

            with mock.patch.object(ftp_module, "remote_size", return_value=os.path.getsize(destination)):
                result = three_dsutil.download_remote_path_to_local(ftp, "/roms/game.nds", "file", temp_dir)

        self.assertIsNone(result)
        ftp.retrbinary.assert_not_called()

    def test_download_remote_path_to_local_renames_different_size_file(self):
        ftp = mock.MagicMock()
        with tempfile.TemporaryDirectory() as temp_dir:
            destination = os.path.join(temp_dir, "game.nds")
            with open(destination, "wb") as file_obj:
                file_obj.write(b"old")

            with mock.patch.object(ftp_module, "remote_size", return_value=999):
                result = three_dsutil.download_remote_path_to_local(ftp, "/roms/game.nds", "file", temp_dir)

        self.assertEqual(os.path.basename(result), "game_1.nds")
        ftp.retrbinary.assert_called_once()

    def test_delete_ftp_path_deletes_files_directly(self):
        ftp = mock.MagicMock()

        three_dsutil.delete_ftp_path(ftp, "/roms/game.nds", "file")

        ftp.delete.assert_called_once_with("/roms/game.nds")
        ftp.rmd.assert_not_called()

    def test_delete_ftp_path_deletes_directory_recursively(self):
        ftp = mock.MagicMock()

        def fake_list(_ftp, path):
            if path == "/roms":
                return [
                    {"name": "..", "type": "dir", "size": None, "modify": None},
                    {"name": "game.nds", "type": "file", "size": "1", "modify": None},
                    {"name": "nested", "type": "dir", "size": None, "modify": None},
                ]
            if path == "/roms/nested":
                return [
                    {"name": "..", "type": "dir", "size": None, "modify": None},
                    {"name": "save.sav", "type": "file", "size": "1", "modify": None},
                ]
            return [{"name": "..", "type": "dir", "size": None, "modify": None}]

        with mock.patch.object(three_dsutil, "list_ftp_directory", side_effect=fake_list):
            three_dsutil.delete_ftp_path(ftp, "/roms", "dir")

        ftp.delete.assert_has_calls([
            mock.call("/roms/game.nds"),
            mock.call("/roms/nested/save.sav"),
        ])
        ftp.rmd.assert_has_calls([
            mock.call("/roms/nested"),
            mock.call("/roms"),
        ])


class FTPHostResolutionTests(unittest.TestCase):
    def test_resolve_ftp_host_prompts_when_interactive_without_host(self):
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(builtins, "input", return_value="192.168.0.60:6000"), \
            mock.patch.object(three_dsutil, "resolve_host", return_value="192.168.0.60"):
            host, port = three_dsutil.resolve_ftp_host(None, 5000, stdin=stdin)

        self.assertEqual((host, port), ("192.168.0.60", 6000))

    def test_resolve_ftp_host_errors_when_noninteractive_without_host(self):
        with self.assertRaises(three_dsutil.DiscoveryError):
            three_dsutil.resolve_ftp_host(None, 5000, stdin=io.StringIO(""))


class StatusTests(unittest.TestCase):
    def test_check_tcp_status_connects_to_host_and_port(self):
        sock = mock.MagicMock()
        sock.__enter__.return_value = sock
        sock.__exit__.return_value = False

        with mock.patch.object(three_dsutil.socket, "create_connection", return_value=sock) as connect_mock, \
            mock.patch("sys.stdout", new_callable=io.StringIO) as stdout:
            three_dsutil.check_tcp_status("192.168.0.10", 17491, "NetLoader")

        connect_mock.assert_called_once_with(("192.168.0.10", 17491), timeout=three_dsutil.DEFAULT_TIMEOUT)
        sock.settimeout.assert_called_once_with(three_dsutil.DEFAULT_TIMEOUT)
        self.assertIn("NetLoader is reachable at 192.168.0.10:17491", stdout.getvalue())
        self.assertIn("Restart it before loading a .3dsx file", stdout.getvalue())

    def test_check_tcp_status_wraps_socket_errors(self):
        with mock.patch.object(three_dsutil.socket, "create_connection", side_effect=OSError("refused")):
            with self.assertRaises(three_dsutil.NetloaderError):
                three_dsutil.check_tcp_status("192.168.0.10", 17491, "NetLoader")

    def test_check_ftp_status_connects_and_logs_in(self):
        ftp = mock.MagicMock()
        ftp.__enter__.return_value = ftp
        ftp.__exit__.return_value = False

        with mock.patch.object(three_dsutil.ftplib, "FTP", return_value=ftp):
            three_dsutil.check_ftp_status("192.168.0.10", 5000, "user", "secret")

        ftp.connect.assert_called_once_with("192.168.0.10", 5000, timeout=three_dsutil.DEFAULT_TIMEOUT)
        ftp.set_pasv.assert_called_once_with(True)
        ftp.login.assert_called_once_with(user="user", passwd="secret")

    def test_run_status_uses_netloader_status(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, host="3ds.local", port=17491)

        with mock.patch.object(three_dsutil, "resolve_netloader_host", return_value=("192.168.0.10", 17491)) as resolve_mock, \
            mock.patch.object(three_dsutil, "check_tcp_status") as status_mock:
            three_dsutil.run_status(args)

        resolve_mock.assert_called_once_with(args)
        status_mock.assert_called_once_with("192.168.0.10", 17491, "NetLoader")

    def test_run_status_uses_ftp_status(self):
        args = argparse.Namespace(
            command=three_dsutil.FTP_COMMAND,
            host="3ds.local",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dsutil, "resolve_ftp_host", return_value=("192.168.0.10", 5000)) as resolve_mock, \
            mock.patch.object(three_dsutil, "check_ftp_status") as status_mock:
            three_dsutil.run_status(args)

        resolve_mock.assert_called_once_with("3ds.local", 5000)
        status_mock.assert_called_once_with("192.168.0.10", 5000, "anonymous", "")


class ManagementCommandTests(unittest.TestCase):
    def make_args(self, temp_dir, command=three_dsutil.INSTALL_COMMAND):
        return argparse.Namespace(
            command=command,
            repo_url="https://example.test/3dsutil.py.git",
            ref="",
            install_root=os.path.join(temp_dir, "lib", "3dsutil.py"),
            bin_dir=os.path.join(temp_dir, "bin"),
            python=sys.executable,
        )

    def test_write_launcher_creates_executable_3dsutil_command(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            install_root = os.path.join(temp_dir, "lib", "3dsutil.py")
            bin_dir = os.path.join(temp_dir, "bin")
            os.makedirs(install_root)

            path = three_dsutil.write_launcher(bin_dir, install_root, sys.executable)

            self.assertEqual(path, os.path.join(bin_dir, "3dsutil"))
            self.assertTrue(os.access(path, os.X_OK))
            with open(path, encoding="utf-8") as file_obj:
                launcher = file_obj.read()
            self.assertIn("3dsutil.py", launcher)

    def test_run_install_clones_and_writes_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
            mock.patch.object(three_dsutil, "ensure_git_available") as git_mock, \
            mock.patch.object(three_dsutil.subprocess, "run") as run_mock:
            args = self.make_args(temp_dir)

            three_dsutil.run_install(args)

            git_mock.assert_called_once()
            run_mock.assert_called_once_with(["git", "clone", args.repo_url, args.install_root], check=True)
            self.assertTrue(os.path.exists(os.path.join(args.bin_dir, "3dsutil")))

    def test_run_install_checks_out_ref_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
            mock.patch.object(three_dsutil, "ensure_git_available"), \
            mock.patch.object(three_dsutil.subprocess, "run") as run_mock:
            args = self.make_args(temp_dir)
            args.ref = "1.1"

            three_dsutil.run_install(args)

            run_mock.assert_has_calls([
                mock.call(["git", "clone", args.repo_url, args.install_root], check=True),
                mock.call(["git", "-C", args.install_root, "fetch", "--tags", "--force"], check=True),
                mock.call(["git", "-C", args.install_root, "checkout", "1.1"], check=True),
            ])

    def test_run_update_pulls_existing_checkout_and_rewrites_launcher(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
            mock.patch.object(three_dsutil, "ensure_git_available") as git_mock, \
            mock.patch.object(three_dsutil.subprocess, "run") as run_mock:
            args = self.make_args(temp_dir, command=three_dsutil.UPDATE_COMMAND)
            os.makedirs(os.path.join(args.install_root, ".git"))

            three_dsutil.run_update(args)

            git_mock.assert_called_once()
            run_mock.assert_called_once_with(["git", "-C", args.install_root, "pull", "--ff-only"], check=True)
            self.assertTrue(os.path.exists(os.path.join(args.bin_dir, "3dsutil")))

    def test_run_update_checks_out_ref_when_requested(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
            mock.patch.object(three_dsutil, "ensure_git_available"), \
            mock.patch.object(three_dsutil.subprocess, "run") as run_mock:
            args = self.make_args(temp_dir, command=three_dsutil.UPDATE_COMMAND)
            args.ref = "1.1"
            os.makedirs(os.path.join(args.install_root, ".git"))

            three_dsutil.run_update(args)

            run_mock.assert_has_calls([
                mock.call(["git", "-C", args.install_root, "pull", "--ff-only"], check=True),
                mock.call(["git", "-C", args.install_root, "fetch", "--tags", "--force"], check=True),
                mock.call(["git", "-C", args.install_root, "checkout", "1.1"], check=True),
            ])

    def test_run_update_rejects_missing_checkout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = self.make_args(temp_dir, command=three_dsutil.UPDATE_COMMAND)

            with self.assertRaises(three_dsutil.NetloaderError):
                three_dsutil.run_update(args)

    def test_run_uninstall_removes_launcher_and_install_root(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            args = argparse.Namespace(
                command=three_dsutil.UNINSTALL_COMMAND,
                install_root=os.path.join(temp_dir, "lib", "3dsutil.py"),
                bin_dir=os.path.join(temp_dir, "bin"),
            )
            os.makedirs(args.install_root)
            os.makedirs(args.bin_dir)
            launcher = os.path.join(args.bin_dir, "3dsutil")
            with open(launcher, "w", encoding="utf-8") as file_obj:
                file_obj.write("#!/bin/sh\n")

            three_dsutil.run_uninstall(args)

            self.assertFalse(os.path.exists(launcher))
            self.assertFalse(os.path.exists(args.install_root))


class MainTests(unittest.TestCase):
    def test_main_discovers_host_then_sends_file(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "discover_3ds", return_value="192.168.0.44") as discover_mock, \
            mock.patch.object(three_dsutil, "send_3dsx") as send_mock:
            result = three_dsutil.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        discover_mock.assert_called_once_with(17491, three_dsutil.DEFAULT_DISCOVERY_RETRIES, 1.0)
        send_mock.assert_called_once_with(host="192.168.0.44", port=17491, path="sample-app.3dsx")

    def test_main_prompts_for_netloader_host_when_discovery_fails_interactively(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)
        stdin = mock.MagicMock()
        stdin.isatty.return_value = True

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "discover_3ds", side_effect=three_dsutil.DiscoveryError("no reply")), \
            mock.patch.object(three_dsutil.sys, "stdin", stdin), \
            mock.patch.object(builtins, "input", return_value="3ds.local:1234"), \
            mock.patch.object(three_dsutil, "resolve_host", return_value="192.168.0.99") as resolve_mock, \
            mock.patch.object(three_dsutil, "send_3dsx") as send_mock:
            result = three_dsutil.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        resolve_mock.assert_called_once_with("3ds.local", 1234)
        send_mock.assert_called_once_with(host="192.168.0.99", port=1234, path="sample-app.3dsx")

    def test_main_keeps_netloader_discovery_error_when_noninteractive(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host=None, port=17491)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "discover_3ds", side_effect=three_dsutil.DiscoveryError("no reply")), \
            mock.patch.object(three_dsutil.sys, "stdin", io.StringIO("")):
            result = three_dsutil.main(["sample-app.3dsx"])

        self.assertEqual(result, 1)

    def test_main_uses_explicit_host_without_discovery(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host="3ds.local", port=17491)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "resolve_host", return_value="192.168.0.99") as resolve_mock, \
            mock.patch.object(three_dsutil, "send_3dsx") as send_mock:
            result = three_dsutil.main(["--host", "3ds.local", "sample-app.3dsx"])

        self.assertEqual(result, 0)
        resolve_mock.assert_called_once_with("3ds.local", 17491)
        send_mock.assert_called_once_with(host="192.168.0.99", port=17491, path="sample-app.3dsx")

    def test_main_returns_1_on_netloader_error(self):
        args = argparse.Namespace(command=three_dsutil.NETLOADER_COMMAND, legacy=False, file="sample-app.3dsx", host="192.168.0.10", port=17491)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "resolve_host", return_value="192.168.0.10"), \
            mock.patch.object(three_dsutil, "send_3dsx", side_effect=three_dsutil.NetloaderError("boom")):
            result = three_dsutil.main(["--host", "192.168.0.10", "sample-app.3dsx"])

        self.assertEqual(result, 1)

    def test_main_warns_for_legacy_form(self):
        with mock.patch.object(three_dsutil, "run_netloader") as run_mock, \
            mock.patch.object(sys, "stderr", new_callable=io.StringIO) as stderr:
            result = three_dsutil.main(["sample-app.3dsx"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once()
        self.assertIn("deprecated", stderr.getvalue())

    def test_main_runs_default_status_without_legacy_warning(self):
        with mock.patch.object(three_dsutil, "run_status") as run_mock, \
            mock.patch.object(sys, "stderr", new_callable=io.StringIO) as stderr:
            result = three_dsutil.main(["status", "--host", "192.168.0.10"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once()
        self.assertNotIn("deprecated", stderr.getvalue())

    def test_main_runs_ftp_command(self):
        args = argparse.Namespace(
            command=three_dsutil.FTP_COMMAND,
            action=three_dsutil.UPLOAD_ACTION,
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

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "run_ftp") as run_mock:
            result = three_dsutil.main(["ftp", "upload", "--host", "192.168.0.10", "--source", "sample-app.3dsx", "--dest", "/3ds/"])

        self.assertEqual(result, 0)
        run_mock.assert_called_once_with(args)

    def test_main_runs_ftp_explorer_command(self):
        args = argparse.Namespace(
            command=three_dsutil.FTP_COMMAND,
            action=three_dsutil.EXPLORER_ACTION,
            legacy=False,
            host="192.168.0.10",
            port=5000,
            user="anonymous",
            password="",
        )

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "run_ftp_explorer") as explorer_mock:
            result = three_dsutil.main(["ftp", "--host", "192.168.0.10"])

        self.assertEqual(result, 0)
        explorer_mock.assert_called_once_with(args)

    def test_main_runs_install_command(self):
        args = argparse.Namespace(command=three_dsutil.INSTALL_COMMAND, action=None, legacy=False)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "run_install") as install_mock:
            result = three_dsutil.main(["install"])

        self.assertEqual(result, 0)
        install_mock.assert_called_once_with(args)

    def test_main_runs_uninstall_command(self):
        args = argparse.Namespace(command=three_dsutil.UNINSTALL_COMMAND, action=None, legacy=False)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "run_uninstall") as uninstall_mock:
            result = three_dsutil.main(["uninstall"])

        self.assertEqual(result, 0)
        uninstall_mock.assert_called_once_with(args)

    def test_main_runs_update_command(self):
        args = argparse.Namespace(command=three_dsutil.UPDATE_COMMAND, action=None, legacy=False)

        with mock.patch.object(three_dsutil, "parse_args", return_value=args), \
            mock.patch.object(three_dsutil, "run_update") as update_mock:
            result = three_dsutil.main(["update"])

        self.assertEqual(result, 0)
        update_mock.assert_called_once_with(args)

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

        with mock.patch.object(three_dsutil, "resolve_ftp_host", return_value=("192.168.0.10", 5000)), \
            mock.patch.object(three_dsutil, "get_ftp_archive_action", return_value=three_dsutil.FTP_ARCHIVE_UNARCHIVE), \
            mock.patch.object(three_dsutil, "unarchive_ftp_sources", return_value="/tmp/extracted") as unarchive_mock, \
            mock.patch.object(three_dsutil, "send_ftp") as send_mock:
            three_dsutil.run_ftp(args)

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

        with mock.patch.object(three_dsutil, "resolve_ftp_host", return_value=("192.168.0.10", 5000)), \
            mock.patch.object(three_dsutil, "get_ftp_archive_action", return_value=three_dsutil.FTP_ARCHIVE_SKIP), \
            mock.patch.object(three_dsutil, "send_ftp") as send_mock:
            three_dsutil.run_ftp(args)

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
