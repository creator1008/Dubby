"""Safe downloader for user-supplied direct media URLs.

Only public HTTP(S) endpoints returning an audio/video payload are accepted.
HTML watch pages (YouTube, Vimeo, etc.) are intentionally rejected; supporting
those requires a provider-specific ingestion service rather than generic HTTP.
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import httpx

_MEDIA_SUFFIXES = {
    ".mp4",
    ".mov",
    ".m4v",
    ".webm",
    ".mkv",
    ".avi",
    ".mp3",
    ".m4a",
    ".wav",
    ".aac",
    ".ogg",
}
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class RemoteMediaError(ValueError):
    pass


@dataclass(frozen=True)
class DownloadedMedia:
    path: Path
    filename: str
    content_type: str
    size_bytes: int


async def _validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise RemoteMediaError("HTTP 또는 HTTPS 영상 링크를 입력해 주세요.")
    if parsed.username or parsed.password:
        raise RemoteMediaError("사용자 정보가 포함된 URL은 지원하지 않습니다.")
    if parsed.port and parsed.port not in {80, 443}:
        raise RemoteMediaError("표준 HTTP/HTTPS 포트만 지원합니다.")
    hostname = parsed.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith(".localhost"):
        raise RemoteMediaError("로컬 네트워크 주소는 사용할 수 없습니다.")
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as exc:
        raise RemoteMediaError("영상 링크의 호스트를 찾을 수 없습니다.") from exc
    if not addresses:
        raise RemoteMediaError("영상 링크의 호스트를 찾을 수 없습니다.")
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global:
            raise RemoteMediaError("사설 또는 로컬 네트워크 주소는 사용할 수 없습니다.")


def _filename_from_response(response: httpx.Response, url: str) -> str:
    disposition = response.headers.get("content-disposition", "")
    match = re.search(
        r"""filename\*?=(?:UTF-8''|")?([^";]+)""",
        disposition,
        flags=re.IGNORECASE,
    )
    candidate = unquote(match.group(1).strip()) if match else ""
    if not candidate:
        candidate = unquote(Path(urlparse(url).path).name)
    candidate = Path(candidate).name
    suffix = Path(candidate).suffix.lower()
    if suffix not in _MEDIA_SUFFIXES:
        content_type = response.headers.get("content-type", "").split(";", 1)[0]
        suffix = {
            "video/mp4": ".mp4",
            "video/webm": ".webm",
            "video/quicktime": ".mov",
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "audio/wav": ".wav",
        }.get(content_type, ".mp4")
        candidate = f"remote-video{suffix}"
    stem = re.sub(r"[^\w.-]+", "_", Path(candidate).stem).strip("._")
    return f"{stem or 'remote-video'}{suffix}"


async def download_remote_media(
    url: str,
    directory: Path,
    *,
    max_bytes: int,
) -> DownloadedMedia:
    """Download one public direct-media URL into ``directory``."""
    current_url = url.strip()
    directory.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(connect=10, read=120, write=30, pool=10)
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": "Dubby/1.0 (+direct-media-import)"},
    ) as client:
        for _ in range(6):
            await _validate_public_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise RemoteMediaError("잘못된 영상 링크 리디렉션입니다.")
                    current_url = urljoin(current_url, location)
                    continue
                if response.status_code >= 400:
                    raise RemoteMediaError(
                        f"영상 링크 요청에 실패했습니다 ({response.status_code})."
                    )
                content_type = (
                    response.headers.get("content-type", "")
                    .split(";", 1)[0]
                    .strip()
                    .lower()
                )
                suffix = Path(urlparse(current_url).path).suffix.lower()
                if not (
                    content_type.startswith(("video/", "audio/"))
                    or (
                        content_type in {"", "application/octet-stream"}
                        and suffix in _MEDIA_SUFFIXES
                    )
                ):
                    raise RemoteMediaError(
                        "직접 재생 가능한 영상 파일 링크만 지원합니다. "
                        "웹페이지 또는 스트리밍 페이지 링크는 사용할 수 없습니다."
                    )
                length = response.headers.get("content-length")
                if length and int(length) > max_bytes:
                    raise RemoteMediaError("링크 영상은 최대 500MB까지 지원합니다.")
                filename = _filename_from_response(response, current_url)
                destination = directory / filename
                size = 0
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > max_bytes:
                            output.close()
                            destination.unlink(missing_ok=True)
                            raise RemoteMediaError(
                                "링크 영상은 최대 500MB까지 지원합니다."
                            )
                        output.write(chunk)
                if size == 0:
                    destination.unlink(missing_ok=True)
                    raise RemoteMediaError("링크에서 받은 영상 파일이 비어 있습니다.")
                return DownloadedMedia(
                    path=destination,
                    filename=filename,
                    content_type=content_type or "application/octet-stream",
                    size_bytes=size,
                )
    raise RemoteMediaError("영상 링크의 리디렉션이 너무 많습니다.")
