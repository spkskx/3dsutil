import argparse
import sys

import ftp as _ftp
from core import (
    DEFAULT_FTP_PORT,
    DEFAULT_DISCOVERY_RETRIES,
    DEFAULT_NETLOADER_PORT,
    DISCOVERY_REQUEST,
    DISCOVERY_RESPONSE_PREFIX,
    EXPLORER_ACTION,
    FTP_ARCHIVE_SKIP,
    FTP_ARCHIVE_UNARCHIVE,
    FTP_COMMAND,
    LOAD_ACTION,
    NETLOADER_COMMAND,
    STATUS_ACTION,
    UPLOAD_ACTION,
    NetloaderError,
    DiscoveryError,
    FTPTransferError,
    parse_host_port,
    resolve_host,
    validate_input_file,
)
from netloader import *
from ftp import *


def resolve_ftp_host(host, port, stdin=sys.stdin):
    if host is not None:
        resolved_host, resolved_port = parse_host_port(host, port, "FTP host")
        return resolve_host(resolved_host, resolved_port), resolved_port

    candidates = discover_ftp_mdns()
    if len(candidates) == 1:
        candidate_host, candidate_port = candidates[0]
        return resolve_host(candidate_host, candidate_port), candidate_port

    if stdin.isatty():
        value = input("Enter 3DS FTP host or host:port: ").strip()
        if not value:
            raise DiscoveryError("FTP host is required")
        prompted_host, prompted_port = parse_host_port(value, port, "FTP host")
        return resolve_host(prompted_host, prompted_port), prompted_port

    raise DiscoveryError("could not resolve a 3DS FTP host. Pass --host, or run interactively to enter host or host:port")


def get_ftp_archive_action(args, stdin=None):
    if args.unarchive:
        return FTP_ARCHIVE_UNARCHIVE
    if stdin is None:
        stdin = sys.stdin
    if not stdin.isatty():
        return _ftp.FTP_ARCHIVE_UPLOAD
    if not has_archive_sources(args.source):
        return _ftp.FTP_ARCHIVE_UPLOAD

    answer = input("Archive files found. Extract archives before upload? [Y/n]: ").strip().lower()
    if answer in ("", "y", "yes"):
        return FTP_ARCHIVE_UNARCHIVE
    return FTP_ARCHIVE_SKIP


def should_unarchive_ftp(args, stdin=None):
    return get_ftp_archive_action(args, stdin=stdin) == FTP_ARCHIVE_UNARCHIVE


def delete_ftp_path(ftp, path, entry_type):
    if entry_type != "dir":
        ftp.delete(path)
        return

    for entry in list_ftp_directory(ftp, path):
        if entry["name"] == "..":
            continue
        child_path = join_ftp_explorer_path(path, entry["name"])
        delete_ftp_path(ftp, child_path, entry["type"])
    ftp.rmd(path)


def add_ftp_connection_arguments(parser):
    parser.add_argument(
        "--host",
        help="3DS FTP hostname or IPv4 address. If omitted, the utility tries mDNS discovery or prompts interactively.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_FTP_PORT,
        help=f"FTP port. Default: {DEFAULT_FTP_PORT}",
    )
    parser.add_argument("--user", default="anonymous", help="FTP username. Default: anonymous")
    parser.add_argument("--password", default="", help="FTP password. Default: empty")


def add_netloader_arguments(parser):
    parser.add_argument("file", help="Path to the .3dsx file to upload")
    parser.add_argument(
        "--host",
        help="3DS hostname or IPv4 address. If omitted, the utility tries UDP discovery on the local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_NETLOADER_PORT,
        help=f"NetLoader port for both discovery and transfer. Default: {DEFAULT_NETLOADER_PORT}",
    )


def add_ftp_arguments(parser):
    parser.add_argument("--source", action="append", required=True, help="Local file or directory to upload. Repeat for multiple sources")
    parser.add_argument("--dest", required=True, help="Remote destination file or directory path")
    parser.add_argument(
        "--unarchive",
        action="store_true",
        help="Extract .zip or .7z archives before uploading. If omitted in an interactive terminal, archives trigger a yes/no prompt.",
    )
    parser.add_argument(
        "--patterns",
        action="append",
        help="Upload only files matching a shell-style pattern, such as '*.nds' or '*.gba'. Repeat for multiple patterns.",
    )
    add_ftp_connection_arguments(parser)


def add_netloader_status_arguments(parser):
    parser.add_argument(
        "--host",
        help="3DS hostname or IPv4 address. If omitted, the utility tries UDP discovery on the local network.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_NETLOADER_PORT,
        help=f"NetLoader port for both discovery and connection check. Default: {DEFAULT_NETLOADER_PORT}",
    )


def add_ftp_status_arguments(parser):
    add_ftp_connection_arguments(parser)


def validate_port(parser, port, option="--port"):
    if not 1 <= port <= 65535:
        parser.error(f"{option} must be between 1 and 65535")


def parse_status_args(argv, command, add_arguments, description):
    parser = argparse.ArgumentParser(description=description)
    add_arguments(parser)
    args = parser.parse_args(argv)
    validate_port(parser, args.port)
    args.command = command
    args.action = STATUS_ACTION
    args.legacy = False
    return args


def parse_action_args(argv, command, action, add_arguments, description):
    parser = argparse.ArgumentParser(description=description)
    add_arguments(parser)
    args = parser.parse_args(argv)
    validate_port(parser, args.port)
    args.command = command
    args.action = action
    args.legacy = False
    return args


def parse_args(argv):
    argv = list(argv)
    parser = argparse.ArgumentParser(
        description="Transfer a .3dsx file to a modded 3DS over NetLoader or FTP.",
    )
    subparsers = parser.add_subparsers(dest="command")

    netloader_parser = subparsers.add_parser(
        NETLOADER_COMMAND,
        help="Upload and launch through 3dslink NetLoader",
        description="Upload and launch one .3dsx file through Homebrew Launcher NetLoader.",
        epilog=(
            "Common commands:\n"
            "  python3 3dslink.py netloader sample-app.3dsx\n"
            "  python3 3dslink.py netloader --host 172.20.10.12 sample-app.3dsx\n"
            "  python3 3dslink.py netloader load --help\n"
            "  python3 3dslink.py netloader status --help"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_netloader_arguments(netloader_parser)

    ftp_parser = subparsers.add_parser(
        FTP_COMMAND,
        help="Browse or upload through a 3DS FTP server such as ftpd",
        description="Browse, upload, or check a 3DS FTP server such as ftpd.",
        epilog=(
            "Common commands:\n"
            "  python3 3dslink.py ftp --host 172.20.10.12\n"
            "  python3 3dslink.py ftp explorer --help\n"
            "  python3 3dslink.py ftp upload --help\n"
            "  python3 3dslink.py ftp status --help"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_ftp_connection_arguments(ftp_parser)

    if argv and argv[0] in ("-h", "--help"):
        return parser.parse_args(argv)

    if argv and argv[0] == STATUS_ACTION:
        args = parse_status_args(
            argv[1:],
            NETLOADER_COMMAND,
            add_netloader_status_arguments,
            "Check NetLoader connection status.",
        )
        args.legacy = True
        return args

    if len(argv) >= 2 and argv[0] == NETLOADER_COMMAND and argv[1] == STATUS_ACTION:
        return parse_status_args(
            argv[2:],
            NETLOADER_COMMAND,
            add_netloader_status_arguments,
            "Check NetLoader connection status.",
        )

    if len(argv) >= 2 and argv[0] == NETLOADER_COMMAND and argv[1] == LOAD_ACTION:
        return parse_action_args(
            argv[2:],
            NETLOADER_COMMAND,
            LOAD_ACTION,
            add_netloader_arguments,
            "Load a .3dsx file through 3dslink NetLoader.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == STATUS_ACTION:
        return parse_status_args(
            argv[2:],
            FTP_COMMAND,
            add_ftp_status_arguments,
            "Check FTP connection status.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == UPLOAD_ACTION:
        return parse_action_args(
            argv[2:],
            FTP_COMMAND,
            UPLOAD_ACTION,
            add_ftp_arguments,
            "Upload a file or directory through FTP.",
        )

    if len(argv) >= 2 and argv[0] == FTP_COMMAND and argv[1] == EXPLORER_ACTION:
        return parse_action_args(
            argv[2:],
            FTP_COMMAND,
            EXPLORER_ACTION,
            add_ftp_connection_arguments,
            "Browse files and directories through FTP.",
        )

    if argv and argv[0] in (NETLOADER_COMMAND, FTP_COMMAND):
        args = parser.parse_args(argv)
        validate_port(parser, args.port)
        if args.command == NETLOADER_COMMAND:
            args.action = LOAD_ACTION
        else:
            args.action = EXPLORER_ACTION
        args.legacy = False
        return args

    legacy_parser = argparse.ArgumentParser(
        description="Transfer a .3dsx file to a modded 3DS over 3dslink NetLoader.",
    )
    add_netloader_arguments(legacy_parser)
    args = legacy_parser.parse_args(argv)
    validate_port(legacy_parser, args.port)
    args.command = NETLOADER_COMMAND
    args.action = LOAD_ACTION
    args.legacy = True

    return args


def resolve_netloader_host(args, stdin=None):
    if stdin is None:
        stdin = sys.stdin

    host = args.host
    if host is None:
        print(f"Discovering a 3DS on the local network via UDP broadcast on port {args.port}...")
        try:
            host = discover_3ds(args.port, DEFAULT_DISCOVERY_RETRIES, 1.0)
            print(f"Discovered 3DS at {host}")
            return host, args.port
        except DiscoveryError:
            if not stdin.isatty():
                raise

            value = input("Enter 3DS NetLoader host or host:port: ").strip()
            if not value:
                raise DiscoveryError("NetLoader host is required")
            prompted_host, prompted_port = parse_host_port(value, args.port, "NetLoader host")
            return resolve_host(prompted_host, prompted_port), prompted_port

    resolved_host, resolved_port = parse_host_port(host, args.port, "NetLoader host")
    return resolve_host(resolved_host, resolved_port), resolved_port


def run_netloader(args):
    host, port = resolve_netloader_host(args)

    send_3dsx(
        host=host,
        port=port,
        path=args.file,
    )
    print("Transfer complete. Check your 3DS screen.")


def run_ftp(args):
    host, port = resolve_ftp_host(args.host, args.port)
    archive_action = get_ftp_archive_action(args)
    if archive_action == FTP_ARCHIVE_UNARCHIVE:
        with tempfile.TemporaryDirectory(prefix="3dslink-ftp-") as temp_dir:
            source = unarchive_ftp_sources(args.source, temp_dir)
            send_ftp(
                host=host,
                port=port,
                source=source,
                dest=args.dest,
                user=args.user,
                password=args.password,
                patterns=args.patterns,
            )
        return

    send_ftp(
        host=host,
        port=port,
        source=args.source,
        dest=args.dest,
        user=args.user,
        password=args.password,
        patterns=args.patterns,
        skip_archives=archive_action == FTP_ARCHIVE_SKIP,
    )


def run_status(args):
    if args.command == FTP_COMMAND:
        host, port = resolve_ftp_host(args.host, args.port)
        check_ftp_status(host, port, args.user, args.password)
        return

    host, port = resolve_netloader_host(args)
    check_tcp_status(host, port, "NetLoader")


def main(argv=None):
    args = parse_args(argv or sys.argv[1:])

    try:
        if getattr(args, "legacy", False) and getattr(args, "action", UPLOAD_ACTION) != STATUS_ACTION:
            print(
                "Warning: running NetLoader without the 'netloader' command is deprecated; use 'netloader' explicitly.",
                file=sys.stderr,
            )

        if getattr(args, "action", UPLOAD_ACTION) == STATUS_ACTION:
            run_status(args)
        elif args.command == FTP_COMMAND and args.action == EXPLORER_ACTION:
            run_ftp_explorer(args)
        elif args.command == FTP_COMMAND:
            run_ftp(args)
        else:
            run_netloader(args)
        return 0
    except NetloaderError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Error: transfer cancelled by user", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
