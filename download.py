import os
import posixpath
import urllib.error
import urllib.parse
import urllib.request

from core import DEFAULT_TIMEOUT, FTPTransferError


DOWNLOAD_CHUNK = 64 * 1024
DOWNLOAD_SCHEMES = ("http", "https")


def validate_download_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme.lower() not in DOWNLOAD_SCHEMES or not parsed.netloc:
        raise FTPTransferError("download URL must be an absolute http:// or https:// URL")
    return url


def download_filename(url, name=None):
    candidate = name if name is not None else urllib.parse.unquote(posixpath.basename(urllib.parse.urlsplit(url).path))
    if not candidate:
        raise FTPTransferError("download URL has no filename; pass --name")
    if candidate in (".", "..") or os.path.basename(candidate) != candidate or "/" in candidate or "\\" in candidate or "\x00" in candidate:
        raise FTPTransferError("download filename must be a single safe filename")
    return candidate


class HttpRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme.lower() not in DOWNLOAD_SCHEMES:
            raise FTPTransferError("download redirect must use http:// or https://")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def print_download_progress(name, received, total):
    if total:
        percent = min(100, int(received * 100 / total))
        print(f"Downloading {name}: {received}/{total} bytes ({percent}%)")
    else:
        print(f"Downloading {name}: {received} bytes")


def download_url(url, destination, name=None, progress=None):
    validate_download_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": "3dsutil"})
    opener = urllib.request.build_opener(
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
        HttpRedirectHandler(),
    )
    try:
        response = opener.open(request, timeout=DEFAULT_TIMEOUT)
    except FTPTransferError:
        raise
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        raise FTPTransferError(f"download failed: {exc}") from exc

    with response:
        final_url = response.geturl()
        validate_download_url(final_url)
        filename = download_filename(final_url, name)
        try:
            total = int(response.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            total = 0
        target = os.path.join(destination, filename)
        received = 0
        last_percent = -1
        try:
            with open(target, "xb") as file_obj:
                while True:
                    chunk = response.read(DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    file_obj.write(chunk)
                    received += len(chunk)
                    if progress is not None:
                        progress(filename, received, total)
                    else:
                        percent = 100 if not total else int(received * 100 / total)
                        if percent == 100 or percent >= last_percent + 10:
                            print_download_progress(filename, received, total)
                            last_percent = percent
        except Exception as exc:
            try:
                os.remove(target)
            except OSError:
                pass
            if isinstance(exc, FTPTransferError):
                raise
            raise FTPTransferError(f"download failed: {exc}") from exc

    if received == 0:
        os.remove(target)
        raise FTPTransferError("downloaded file is empty")
    return target
