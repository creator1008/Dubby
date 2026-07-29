"""Fetch remote media for dubbing: direct MP4/WebM URLs or yt-dlp platforms.

YouTube, Facebook, and TikTok page URLs are downloaded with the ``yt-dlp``
Python package (``pip install yt-dlp``). Direct media URLs use httpx with
SSRF protections (public http/https only, size cap).
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

import httpx

MediaUrlKind = Literal["direct", "ytdlp", "unsupported"]

DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_COOKIE_AUTH_HINT_RE = re.compile(
    r"cookies|log in|login|sign in|comfortable for some audiences|"
    r"age.?restrict|confirm your age|private video",
    re.IGNORECASE,
)
_COOKIE_DB_HINT_RE = re.compile(
    r"could not copy|cookie database|failed to decrypt|failed to load cookies|"
    r"could not find .+ cookies|permission denied",
    re.IGNORECASE,
)
_TIKTOK_EXTRACT_HINT_RE = re.compile(
    r"universal data for rehydration|webpage video data|impersonat",
    re.IGNORECASE,
)

# HTML5 <video> in Chromium/Firefox reliably decodes these; TikTok often
# defaults to HEVC which plays as audio-only in many browsers.
_BROWSER_SAFE_VIDEO_CODECS = frozenset({"h264", "avc1", "vp8", "vp9"})
_NEEDS_TRANSCODE_VIDEO_CODECS = frozenset(
    {"hevc", "h265", "hvc1", "hev1", "av1", "av01", "mpeg4", "mpeg2video"}
)

DIRECT_MEDIA_EXTENSIONS = frozenset(
    {".mp4", ".webm", ".mov", ".m4v", ".mkv", ".m4a", ".mp3", ".wav"}
)

# Host suffixes (lowercase, no port). Match exact host or subdomain.
_YTDLP_HOST_SUFFIXES = (
    "youtube.com",
    "youtu.be",
    "facebook.com",
    "fb.watch",
    "fb.com",
    "tiktok.com",
)

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata",
    }
)


class RemoteMediaError(Exception):
    """User-facing failure while classifying or downloading remote media."""


def _hostname(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    return host


def _host_matches_suffix(host: str, suffix: str) -> bool:
    return host == suffix or host.endswith("." + suffix)


def is_ytdlp_platform_host(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    return any(_host_matches_suffix(host, suffix) for suffix in _YTDLP_HOST_SUFFIXES)


def _path_has_direct_media_extension(path: str) -> bool:
    lower = path.lower()
    # Strip simple query-like suffixes already removed by urlparse.path
    for ext in DIRECT_MEDIA_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False


def classify_media_url(url: str) -> MediaUrlKind:
    """Classify a user-pasted URL as direct media, yt-dlp platform, or unsupported."""
    raw = (url or "").strip()
    if not raw:
        return "unsupported"
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return "unsupported"
    host = _hostname(raw)
    if not host:
        return "unsupported"
    if is_ytdlp_platform_host(host):
        return "ytdlp"
    if _path_has_direct_media_extension(parsed.path or ""):
        return "direct"
    return "unsupported"


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    # Prefer is_global over is_reserved: DNS64/NAT64 answers (64:ff9b::/96) are
    # marked reserved by ipaddress but are globally routable translation prefixes
    # used by public CDNs such as TikTok short links (vt.tiktok.com).
    return not ip.is_global


def assert_public_http_url(url: str) -> None:
    """Reject non-http(s) URLs and hosts that resolve to non-public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise RemoteMediaError("http(s) URL만 지원합니다.")
    if parsed.username or parsed.password:
        raise RemoteMediaError("인증 정보가 포함된 URL은 지원하지 않습니다.")
    host = _hostname(url)
    if not host:
        raise RemoteMediaError("유효하지 않은 URL입니다.")
    if host in _BLOCKED_HOSTNAMES or host.endswith(".localhost"):
        raise RemoteMediaError("내부 주소로의 요청은 허용되지 않습니다.")

    try:
        # Literal IP in the URL
        ip = ipaddress.ip_address(host)
        if _is_blocked_ip(ip):
            raise RemoteMediaError("내부 주소로의 요청은 허용되지 않습니다.")
        return
    except ValueError:
        pass

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise RemoteMediaError("호스트 이름을 확인할 수 없습니다.") from exc
    if not infos:
        raise RemoteMediaError("호스트 이름을 확인할 수 없습니다.")
    for info in infos:
        sockaddr = info[4]
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_blocked_ip(ip):
            raise RemoteMediaError("내부 주소로의 요청은 허용되지 않습니다.")


def assert_safe_direct_media_url(url: str) -> None:
    kind = classify_media_url(url)
    if kind != "direct":
        raise RemoteMediaError(
            "직접 미디어 URL은 .mp4 / .webm 등 확장자가 있는 http(s) 링크여야 합니다."
        )
    assert_public_http_url(url)


def assert_allowed_ytdlp_url(url: str) -> None:
    kind = classify_media_url(url)
    if kind != "ytdlp":
        raise RemoteMediaError(
            "yt-dlp는 YouTube, Facebook, TikTok 페이지 URL만 지원합니다."
        )
    assert_public_http_url(url)


def _extension_from_url_or_type(url: str, content_type: str | None) -> str:
    path = urlparse(url).path.lower()
    for ext in DIRECT_MEDIA_EXTENSIONS:
        if path.endswith(ext):
            return ext
    ctype = (content_type or "").split(";", 1)[0].strip().lower()
    mapping = {
        "video/mp4": ".mp4",
        "video/webm": ".webm",
        "video/quicktime": ".mov",
        "audio/mpeg": ".mp3",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/mp4": ".m4a",
    }
    return mapping.get(ctype, ".mp4")


async def download_direct_media(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Download a direct media URL into ``dest_dir`` with SSRF and size checks."""
    assert_safe_direct_media_url(url)
    dest_dir.mkdir(parents=True, exist_ok=True)

    timeout = httpx.Timeout(DOWNLOAD_TIMEOUT_SECONDS)
    # Do not follow redirects automatically — re-validate each Location.
    async with httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": "DubbyMediaFetcher/1.0"},
    ) as client:
        current = url
        response: httpx.Response | None = None
        for _ in range(5):
            assert_public_http_url(current)
            response = await client.get(current)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise RemoteMediaError("리다이렉트 대상이 없습니다.")
                current = str(httpx.URL(current).join(location))
                continue
            break
        else:
            raise RemoteMediaError("리다이렉트가 너무 많습니다.")

        assert response is not None
        if response.status_code >= 400:
            raise RemoteMediaError(
                f"미디어를 내려받지 못했습니다 (HTTP {response.status_code})."
            )

        content_length = response.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    raise RemoteMediaError(
                        f"파일이 너무 큽니다 (최대 {max_bytes // (1024 * 1024)}MB)."
                    )
            except ValueError:
                pass

        ext = _extension_from_url_or_type(current, response.headers.get("content-type"))
        target = dest_dir / f"source{ext}"
        size = 0
        with target.open("wb") as output:
            async for chunk in response.aiter_bytes(chunk_size=1024 * 256):
                size += len(chunk)
                if size > max_bytes:
                    output.close()
                    target.unlink(missing_ok=True)
                    raise RemoteMediaError(
                        f"파일이 너무 큽니다 (최대 {max_bytes // (1024 * 1024)}MB)."
                    )
                output.write(chunk)
        if size == 0:
            target.unlink(missing_ok=True)
            raise RemoteMediaError("빈 파일입니다.")
        if target.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv", ".webm"}:
            target = ensure_browser_compatible_video(target)
            if target.stat().st_size > max_bytes:
                target.unlink(missing_ok=True)
                raise RemoteMediaError(
                    f"파일이 너무 큽니다 (최대 {max_bytes // (1024 * 1024)}MB)."
                )
        return target


def _require_yt_dlp():
    try:
        import yt_dlp  # type: ignore[import-untyped]
    except ImportError as exc:
        raise RemoteMediaError(
            "yt-dlp가 설치되어 있지 않습니다. "
            "`pip install yt-dlp`로 설치한 뒤 다시 시도해 주세요."
        ) from exc
    return yt_dlp


def _ffmpeg_bin() -> str | None:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg  # type: ignore[import-not-found]

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _ffprobe_bin() -> str | None:
    found = shutil.which("ffprobe")
    if found:
        return found
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None
    sibling = Path(ffmpeg).with_name(
        "ffprobe.exe" if Path(ffmpeg).suffix.lower() == ".exe" else "ffprobe"
    )
    return str(sibling) if sibling.is_file() else None


def _probe_video_codec(path: Path) -> str | None:
    ffprobe = _ffprobe_bin()
    if not ffprobe:
        return None
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=codec_name",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        return None
    codec = (result.stdout or "").strip().lower()
    return codec or None


def ensure_browser_compatible_video(path: Path) -> Path:
    """Re-encode non-HTML5-friendly video (e.g. TikTok HEVC) to H.264+AAC MP4."""
    codec = _probe_video_codec(path)
    if codec is None:
        return path
    if codec in _BROWSER_SAFE_VIDEO_CODECS:
        return path
    if codec not in _NEEDS_TRANSCODE_VIDEO_CODECS and not codec.startswith("hevc"):
        return path

    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        raise RemoteMediaError(
            "브라우저에서 재생할 수 없는 영상 코덱(HEVC 등)입니다. "
            "ffmpeg를 설치한 뒤 다시 시도해 주세요."
        )

    tmp = path.with_name(f"{path.stem}.h264{path.suffix or '.mp4'}")
    if tmp.exists():
        tmp.unlink()
    result = subprocess.run(
        [
            ffmpeg,
            "-y",
            "-nostdin",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "23",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(tmp),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0 or not tmp.is_file() or tmp.stat().st_size <= 0:
        tmp.unlink(missing_ok=True)
        raise RemoteMediaError(
            "브라우저 재생용 H.264 변환에 실패했습니다. "
            f"{(result.stderr or '')[-300:]}"
        )
    path.unlink(missing_ok=True)
    tmp.replace(path)
    return path


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", text).strip()


def _is_cookie_auth_error(message: str) -> bool:
    return bool(_COOKIE_AUTH_HINT_RE.search(message))


def _ytdlp_cookie_option_sets() -> list[dict]:
    """Build cookie option dicts to try, in order.

    Explicit env wins. On local runs, also fall back to common browsers when
    TikTok/YouTube demand a logged-in session (age / sensitive content gates).
    """
    options: list[dict] = []
    cookies_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    from_browser = os.getenv("YTDLP_COOKIES_FROM_BROWSER", "").strip()
    if cookies_file:
        options.append({"cookiefile": cookies_file})
    if from_browser:
        browser, _, profile = from_browser.partition(":")
        browser = browser.strip().lower()
        profile = profile.strip() or None
        if browser:
            # yt-dlp: (name, profile, keyring, container)
            options.append({"cookiesfrombrowser": (browser, profile, None, None)})

    app_env = os.getenv("APP_ENV", "local").strip().lower() or "local"
    auto_browser = os.getenv("YTDLP_COOKIES_AUTO_BROWSER", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if app_env == "local" and auto_browser:
        seen = {
            (opts.get("cookiesfrombrowser") or (None,))[0]
            for opts in options
            if "cookiesfrombrowser" in opts
        }
        # Windows users often have Edge; Chrome/Firefox are common elsewhere.
        for browser in ("edge", "chrome", "firefox", "brave"):
            if browser in seen:
                continue
            options.append({"cookiesfrombrowser": (browser, None, None, None)})
    return options


def _ytdlp_attempt_cookie_opts(url: str) -> list[dict]:
    """Order cookie attempts for platform gates.

    TikTok WAF/challenge solving works best with curl_cffi impersonation and
    *without* stale cookies first — outdated cookie jars often break
    ``universal data for rehydration``. Fall back to browser/file cookies
    when the anonymous fetch hits an age/login wall.
    """
    cookie_sets = _ytdlp_cookie_option_sets()
    host = _hostname(url)
    is_tiktok = bool(host) and (
        host == "tiktok.com" or host.endswith(".tiktok.com")
    )
    attempts: list[dict] = [{}]
    for opts in cookie_sets:
        if opts not in attempts:
            attempts.append(opts)
    if is_tiktok:
        # Prefer anonymous+impersonate, then each cookie source.
        return attempts
    return attempts


def _ytdlp_impersonate_targets() -> list[str]:
    """Browser targets to try when curl_cffi is available."""
    configured = os.getenv("YTDLP_IMPERSONATE", "").strip()
    if configured:
        return [configured]
    return ["chrome", "chrome-110", "safari"]


def _open_ytdlp(yt_dlp_mod, opts: dict):
    """Construct YoutubeDL, dropping impersonate if the target is unavailable."""
    try:
        return yt_dlp_mod.YoutubeDL(opts)
    except Exception:
        if "impersonate" not in opts:
            raise
        fallback = {key: value for key, value in opts.items() if key != "impersonate"}
        return yt_dlp_mod.YoutubeDL(fallback)


def _raise_ytdlp_error(exc: BaseException) -> None:
    message = _strip_ansi(str(exc).strip() or exc.__class__.__name__)
    lower = message.lower()
    if _COOKIE_DB_HINT_RE.search(message):
        raise RemoteMediaError(
            "브라우저 쿠키 DB를 읽지 못했습니다. Chrome/Edge를 모두 종료한 뒤 "
            "다시 시도하거나, TikTok에 로그인한 상태로 해당 영상을 연 다음 "
            "cookies.txt를 내보내 api/.env에 YTDLP_COOKIES_FILE=경로 를 "
            "설정해 주세요."
        ) from exc
    if _is_cookie_auth_error(message):
        raise RemoteMediaError(
            "이 영상은 로그인·연령/민감 콘텐츠 제한으로 쿠키가 필요합니다. "
            "TikTok에 로그인한 브라우저에서 영상을 연 뒤, "
            "api/.env에 YTDLP_COOKIES_FROM_BROWSER=edge "
            "(또는 chrome)을 설정하고 브라우저를 종료한 상태에서 "
            "서버를 재시작해 주세요. 더 안정적으로는 cookies.txt를 내보내 "
            "YTDLP_COOKIES_FILE=경로 로 지정하세요."
        ) from exc
    if _TIKTOK_EXTRACT_HINT_RE.search(message):
        raise RemoteMediaError(
            "TikTok 페이지 추출에 실패했습니다. "
            "`pip install -U yt-dlp curl_cffi` 후 서버를 재시작하고, "
            "TikTok 로그인 상태로 해당 영상을 브라우저에서 연 다음 "
            "cookies.txt를 YTDLP_COOKIES_FILE로 지정해 보세요."
        ) from exc
    if "sign in" in lower or "private" in lower or "login" in lower:
        raise RemoteMediaError(
            "비공개·로그인 필요·연령 제한 영상은 다운로드할 수 없습니다."
        ) from exc
    if "geo" in lower or "not available" in lower:
        raise RemoteMediaError(
            "지역 제한 등으로 이 영상을 다운로드할 수 없습니다."
        ) from exc
    if "ip address is blocked" in lower or "your ip" in lower:
        raise RemoteMediaError(
            "TikTok이 이 PC의 IP를 차단했습니다. "
            "VPN/다른 네트워크로 바꾸거나, 로그인된 브라우저 cookies.txt를 "
            "YTDLP_COOKIES_FILE로 지정한 뒤 다시 시도해 주세요."
        ) from exc
    raise RemoteMediaError(f"yt-dlp 다운로드 실패: {message[:400]}") from exc


def _base_ytdlp_opts(outtmpl: str, max_bytes: int, *, impersonate: str | None = None) -> dict:
    opts: dict = {
        "outtmpl": outtmpl,
        "format": (
            "b[ext=mp4][vcodec^=avc1]/b[ext=mp4][vcodec^=h264]/"
            "bv*[vcodec^=avc1]+ba/bv*[vcodec^=h264]+ba/"
            "bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/"
            "bv*+ba/b[ext=webm]/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "socket_timeout": 30,
        "max_filesize": max_bytes,
    }
    if impersonate:
        opts["impersonate"] = impersonate
    else:
        try:
            import curl_cffi  # noqa: F401

            opts["impersonate"] = _ytdlp_impersonate_targets()[0]
        except ImportError:
            pass
    return opts


def download_with_ytdlp(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Download a YouTube / Facebook / TikTok page URL via yt-dlp into ``dest_dir``."""
    assert_allowed_ytdlp_url(url)
    yt_dlp = _require_yt_dlp()
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "source.%(ext)s")

    # Prefer browser-safe H.264 progressive MP4 (TikTok's best quality is often
    # HEVC, which many browsers play as audio-only).
    try:
        import curl_cffi  # noqa: F401

        impersonate_targets: list[str | None] = list(_ytdlp_impersonate_targets())
    except ImportError:
        impersonate_targets = [None]

    attempt_opts = _ytdlp_attempt_cookie_opts(url)

    info = None
    prepared = Path(outtmpl)
    last_error: BaseException | None = None
    for impersonate in impersonate_targets:
        base_opts = _base_ytdlp_opts(outtmpl, max_bytes, impersonate=impersonate)
        for cookie_opts in attempt_opts:
            ydl_opts = {**base_opts, **cookie_opts}
            try:
                with _open_ytdlp(yt_dlp, ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=True)
                    if info is None:
                        raise RemoteMediaError("영상을 찾을 수 없습니다.")
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    prepared = Path(ydl.prepare_filename(info))
                last_error = None
                break
            except RemoteMediaError:
                raise
            except Exception as exc:  # noqa: BLE001 - surface yt-dlp errors clearly
                last_error = exc
                message = _strip_ansi(str(exc))
                lower = message.lower()
                # Cookie/profile locked or missing browser → try next option.
                if cookie_opts and _COOKIE_DB_HINT_RE.search(message):
                    continue
                if _is_cookie_auth_error(message):
                    # Public fetch failed or cookies insufficient — try next source.
                    continue
                if _TIKTOK_EXTRACT_HINT_RE.search(message):
                    # Stale cookies / wrong impersonate — try next combo.
                    continue
                if "unsupported" in lower and "impersonat" in lower:
                    continue
                if cookie_opts and (
                    "assertionerror" in lower or message in {"", "AssertionError"}
                ):
                    continue
                _raise_ytdlp_error(exc)
        if last_error is None and info is not None:
            break

    if last_error is not None or info is None:
        if last_error is not None:
            _raise_ytdlp_error(last_error)
        raise RemoteMediaError("영상을 찾을 수 없습니다.")

    candidates = [
        prepared,
        prepared.with_suffix(".mp4"),
        prepared.with_suffix(".webm"),
        prepared.with_suffix(".mkv"),
    ]

    target: Path | None = None
    for candidate in candidates:
        if candidate.is_file():
            target = candidate
            break
    if target is None:
        # Fall back to newest source.* file written in dest_dir.
        matches = sorted(
            dest_dir.glob("source.*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        matches = [p for p in matches if p.is_file() and p.suffix.lower() != ".part"]
        if not matches:
            raise RemoteMediaError("다운로드된 영상 파일을 찾지 못했습니다.")
        target = matches[0]

    size = target.stat().st_size
    if size <= 0:
        target.unlink(missing_ok=True)
        raise RemoteMediaError("빈 파일입니다.")
    if size > max_bytes:
        target.unlink(missing_ok=True)
        raise RemoteMediaError(
            f"파일이 너무 큽니다 (최대 {max_bytes // (1024 * 1024)}MB)."
        )

    # Normalize to source.mp4 / source.webm when possible for the pipeline.
    preferred = dest_dir / f"source{target.suffix.lower() or '.mp4'}"
    if target.resolve() != preferred.resolve():
        if preferred.exists():
            preferred.unlink()
        target.replace(preferred)
        target = preferred

    if target.suffix.lower() in {".mp4", ".mov", ".m4v", ".mkv", ".webm"}:
        target = ensure_browser_compatible_video(target)
        if target.stat().st_size > max_bytes:
            target.unlink(missing_ok=True)
            raise RemoteMediaError(
                f"파일이 너무 큽니다 (최대 {max_bytes // (1024 * 1024)}MB)."
            )
    return target


async def ingest_remote_media(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Classify ``url`` and download into ``dest_dir`` as ``source.*``."""
    kind = classify_media_url(url)
    if kind == "ytdlp":
        return await asyncio.to_thread(
            download_with_ytdlp, url, dest_dir, max_bytes=max_bytes
        )
    if kind == "direct":
        return await download_direct_media(url, dest_dir, max_bytes=max_bytes)
    raise RemoteMediaError(
        "지원하지 않는 링크입니다. YouTube / Facebook / TikTok 페이지 URL 또는 "
        "직접 MP4·WebM 미디어 URL을 입력해 주세요."
    )
