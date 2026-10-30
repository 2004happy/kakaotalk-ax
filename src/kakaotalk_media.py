"""Retrieve only media referenced by an existing local KakaoTalk message."""

import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from kakaotalk_service import media_entries, number, visible_media


MAX_BYTES = 25 * 1024 * 1024


def validate_url(url):
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        valid = (parsed.scheme == "https" and not parsed.username and not parsed.password
                 and parsed.port in (None, 443)
                 and (host == "kakaocdn.net" or host.endswith(".kakaocdn.net")))
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Only HTTPS media URLs under kakaocdn.net are allowed")
    return url


class CDNRedirects(HTTPRedirectHandler):
    max_redirections = 3
    max_repeats = 1

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        validate_url(newurl)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def image_mime(data):
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def cached_data(root, reference, max_bytes):
    manifest = root / (reference + ".json")
    try:
        if manifest.is_symlink():
            return None
        value = json.loads(manifest.read_text())
        filename = value.get("file", "")
        if not re.fullmatch(r"[a-f0-9]{64}\.(jpg|png|gif|webp|bin)", filename):
            return None
        path = root / filename
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise ValueError("Media exceeds max_bytes")
        return data if hashlib.sha256(data).hexdigest() == path.stem else None
    except (OSError, json.JSONDecodeError, TypeError, AttributeError):
        return None


def retrieve(reader, room_id, log_id, index=0, download=True, max_bytes=MAX_BYTES):
    number(index, "index")
    number(max_bytes, "max_bytes", 1, MAX_BYTES)
    message = reader.raw_message(room_id, log_id)
    entries = media_entries(message)
    if index >= len(entries):
        raise ValueError("Media index not found; inspect kakaotalk_media first")
    item = entries[index]
    metadata = visible_media([item])[0]
    root = Path.home() / ".cache/kakaotalk-reader/media"
    reference = hashlib.sha256(f"{room_id}:{log_id}:{index}:{item.get('_url', '')}:{item.get('_path', '')}".encode()).hexdigest()
    source_path = None
    if item.get("_path"):
        value = item["_path"]
        parsed = urlsplit(value)
        if parsed.scheme == "file" and parsed.netloc not in ("", "localhost"):
            raise ValueError("Remote file paths are not supported")
        source_path = Path(unquote(parsed.path) if parsed.scheme == "file" else value).expanduser()
        if not source_path.is_absolute():
            source_path = None
    if source_path and source_path.is_file() and not source_path.is_symlink():
        with source_path.open("rb") as handle:
            data = handle.read(max_bytes + 1)
        origin = "local_file"
    elif (cached := cached_data(root, reference, max_bytes)) is not None:
        data, origin = cached, "media_cache"
    elif not download:
        return {"status": "not_downloaded", "media": metadata}, None
    else:
        url = item.get("_url")
        if not url:
            return {"status": "media_unavailable", "detail": "No local file or download URL is stored", "media": metadata}, None
        validate_url(url)
        started = time.monotonic()
        try:
            request = Request(url, headers={"User-Agent": "kakaotalk-reader/0.2", "Accept": "*/*"})
            with build_opener(CDNRedirects()).open(request, timeout=10) as response:
                validate_url(response.url)
                length = response.headers.get("Content-Length")
                if length and length.isdecimal() and int(length) > max_bytes:
                    raise ValueError("Media exceeds max_bytes")
                chunks, size = [], 0
                while True:
                    if time.monotonic() - started > 20:
                        raise TimeoutError
                    chunk = response.read(min(262144, max_bytes + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValueError("Media exceeds max_bytes")
                data = b"".join(chunks)
        except HTTPError as error:
            return {"status": "media_unavailable", "httpStatus": error.code,
                    "detail": "The Kakao CDN link may have expired or require retrieval in the app", "media": metadata}, None
        except (URLError, TimeoutError, OSError):
            return {"status": "media_unavailable", "detail": "The media download failed or timed out", "media": metadata}, None
        origin = "kakao_cdn"
    if len(data) > max_bytes:
        raise ValueError("Media exceeds max_bytes")
    mime = image_mime(data) or item.get("mimeType") or mimetypes.guess_type(item.get("filename", ""))[0] or "application/octet-stream"
    # Do not trust a declared image MIME type for embedding unvalidated content.
    embeddable = image_mime(data)
    suffix = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}.get(embeddable, ".bin")
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    digest = hashlib.sha256(data).hexdigest()
    target = root / (digest + suffix)
    if origin != "media_cache":
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as handle:
            temp = Path(handle.name)
            try:
                os.fchmod(handle.fileno(), 0o600)
                handle.write(data)
                handle.flush()
                os.replace(temp, target)
            finally:
                temp.unlink(missing_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", dir=root, delete=False) as handle:
            temp = Path(handle.name)
            try:
                os.fchmod(handle.fileno(), 0o600)
                json.dump({"file": target.name}, handle)
                handle.flush()
                os.replace(temp, root / (reference + ".json"))
            finally:
                temp.unlink(missing_ok=True)
    return {"status": "ok", "source": origin, "media": metadata, "mimeType": mime,
            "size": len(data), "path": str(target), "imageEmbeddable": bool(embeddable and len(data) <= 8 * 1024 * 1024)}, data
