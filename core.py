import os
import socket

NETLOADER_COMMAND = "netloader"
FTP_COMMAND = "ftp"
INSTALL_COMMAND = "install"
UNINSTALL_COMMAND = "uninstall"
UPDATE_COMMAND = "update"
STATUS_ACTION = "status"
LOAD_ACTION = "load"
UPLOAD_ACTION = "upload"
EXPLORER_ACTION = "explorer"
DEFAULT_NETLOADER_PORT = 17491
DEFAULT_FTP_PORT = 5000
DEFAULT_PORT = DEFAULT_NETLOADER_PORT
DEFAULT_TIMEOUT = 30.0
DEFAULT_DISCOVERY_RETRIES = 10
DISCOVERY_REQUEST = b"3dsboot"
DISCOVERY_RESPONSE_PREFIX = b"boot3ds"
ZLIB_CHUNK = 16 * 1024
FTP_CHUNK = 16 * 1024
FTP_ARCHIVE_UNARCHIVE = "unarchive"
FTP_ARCHIVE_UPLOAD = "upload"
FTP_ARCHIVE_SKIP = "skip"
SEVEN_ZIP_COMMANDS = ("7zz", "7z")
THREEDSX_MAGIC = b"3DSX"
MDNS_ADDR = "224.0.0.251"
MDNS_PORT = 5353
MDNS_FTP_QUERIES = ("_ftp._tcp.local", "_ftpd._tcp.local")
MDNS_DISCOVERY_TIMEOUT = 2.0
MDNS_QTYPE_PTR = 12
MDNS_QTYPE_SRV = 33
MDNS_QTYPE_A = 1
MDNS_QCLASS_IN = 1

NETLOADER_ERRORS = {
    -1: "failed to create file on the 3DS",
    -2: "insufficient free space on the 3DS",
    -3: "insufficient memory on the 3DS",
}


class NetloaderError(Exception):
    pass


class DiscoveryError(NetloaderError):
    pass


class FTPTransferError(NetloaderError):
    pass


def parse_host_port(value, default_port, label="host"):
    host, separator, port_text = value.rpartition(':')
    if not separator:
        return value, default_port

    try:
        port = int(port_text)
    except ValueError as exc:
        raise NetloaderError(f"{label} prompt must be a host or host:port") from exc
    if not 1 <= port <= 65535:
        raise NetloaderError(f"{label} port must be between 1 and 65535")

    return host, port


def resolve_host(host, port):
    try:
        info = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise NetloaderError(f"could not resolve '{host}': {exc}") from exc

    return info[0][4][0]


def validate_input_file(path):
    if not os.path.exists(path):
        raise NetloaderError(f"file not found: {path}")
    if not os.path.isfile(path):
        raise NetloaderError(f"path is not a file: {path}")
    if os.path.getsize(path) == 0:
        raise NetloaderError(f"file is empty: {path}")
