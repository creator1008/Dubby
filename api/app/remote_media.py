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
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

MediaUrlKind = Literal["direct", "ytdlp", "unsupported"]

DEFAULT_MAX_BYTES = 500 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 300.0
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
_COOKIE_AUTH_HINT_RE = re.compile(
    r"comfortable for some audiences|age.?restrict|confirm your age|"
    r"private video|this video is private|members.?only|join this channel|"
    r"login required|please log in|log in for access",
    re.IGNORECASE,
)
_YOUTUBE_BOT_HINT_RE = re.compile(
    r"not a bot|confirm you.?re not a bot|confirm you are not a bot",
    re.IGNORECASE,
)
_COOKIE_DB_HINT_RE = re.compile(
    r"could not copy|cookie database|failed to decrypt|failed to load cookies|"
    r"could not find .+ cookies|permission denied",
    re.IGNORECASE,
)
_TIKTOK_EXTRACT_HINT_RE = re.compile(
    r"universal data for rehydration|webpage video data|"
    r"unexpected response from webpage|impersonat",
    re.IGNORECASE,
)
_FACEBOOK_SHARE_PATH_RE = re.compile(
    r"^/share/(?:r|v|p)/[^/]+/?",
    re.IGNORECASE,
)
_FACEBOOK_JUNK_QUERY = frozenset(
    {
        "_fb_noscript",
        "_rdr",
        "rdid",
        "share_url",
        # Keep mibextid — some FB mobile redirects break when it is stripped.
        "refsrc",
        "ref",
        "aref",
    }
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

# YouTube InnerTube clients. Default (None) first — that is the path that
# previously worked with Chrome TLS impersonate. Forced android/ios-first
# often returns LOGIN_REQUIRED on datacenter IPs and skips working clients.
_YOUTUBE_PLAYER_CLIENT_SETS: tuple[tuple[str, ...] | None, ...] = (
    None,
    ("android",),
    ("web_embedded", "tv_embedded", "tv"),
    ("ios", "mweb"),
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


def _is_youtube_host(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    return any(_host_matches_suffix(host, suffix) for suffix in ("youtube.com", "youtu.be"))


def is_ytdlp_platform_host(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    if not host:
        return False
    return any(_host_matches_suffix(host, suffix) for suffix in _YTDLP_HOST_SUFFIXES)


def _is_tiktok_host(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    return _host_matches_suffix(host, "tiktok.com")


def _is_facebook_host(host: str) -> bool:
    host = host.strip().lower().rstrip(".")
    return any(
        _host_matches_suffix(host, suffix)
        for suffix in ("facebook.com", "fb.watch", "fb.com")
    )


def _is_facebook_share_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").strip().lower()
    if not _is_facebook_host(host):
        return False
    return bool(_FACEBOOK_SHARE_PATH_RE.match(parsed.path or ""))


def normalize_remote_media_url(url: str) -> str:
    """Clean tracker/noscript query junk that breaks Facebook share extraction."""
    raw = (url or "").strip()
    if not raw:
        return raw
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        return raw
    host = (parsed.hostname or "").strip().lower()
    if not _is_facebook_host(host):
        return raw

    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _FACEBOOK_JUNK_QUERY
    ]
    path = parsed.path or "/"
    # Keep a trailing slash on /share/r/<id> so generic+cookie extractors behave.
    if _FACEBOOK_SHARE_PATH_RE.match(path) and not path.endswith("/"):
        path = path + "/"
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            path,
            "",
            urlencode(query),
            "",
        )
    )


def _path_has_direct_media_extension(path: str) -> bool:
    lower = path.lower()
    # Strip simple query-like suffixes already removed by urlparse.path
    for ext in DIRECT_MEDIA_EXTENSIONS:
        if lower.endswith(ext):
            return True
    return False


def classify_media_url(url: str) -> MediaUrlKind:
    """Classify a user-pasted URL as direct media, yt-dlp platform, or unsupported."""
    raw = normalize_remote_media_url((url or "").strip())
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


def _is_youtube_bot_check(message: str) -> bool:
    return bool(_YOUTUBE_BOT_HINT_RE.search(message))


def _ytdlp_cookie_option_sets() -> list[dict]:
    """Build cookie option dicts to try, in order.

    Explicit env wins. On local runs, also fall back to common browsers when
    TikTok/YouTube demand a logged-in session (age / sensitive content gates).
    """
    options: list[dict] = []
    cookies_file = os.getenv("YTDLP_COOKIES_FILE", "").strip()
    app_env = os.getenv("APP_ENV", "local").strip().lower() or "local"
    if not cookies_file and app_env == "local":
        # Local convenience: Playwright export (scripts/export_facebook_cookies.py).
        for candidate in (
            Path(__file__).resolve().parent.parent / "fb-cookies.txt",
            Path(__file__).resolve().parent.parent / "cookies.txt",
        ):
            if candidate.is_file() and candidate.stat().st_size > 0:
                cookies_file = str(candidate)
                break
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

    Facebook ``/share/r/`` links usually need a logged-in session; try cookies
    before the anonymous noscript dead-end.
    """
    cookie_sets = _ytdlp_cookie_option_sets()
    host = _hostname(url)
    is_tiktok = bool(host) and _is_tiktok_host(host)
    is_facebook = bool(host) and _is_facebook_host(host)
    attempts: list[dict] = [{}]
    for opts in cookie_sets:
        if opts not in attempts:
            attempts.append(opts)
    if is_tiktok:
        # Prefer anonymous+impersonate, then each cookie source.
        return attempts
    if is_facebook:
        ordered: list[dict] = []
        for opts in cookie_sets:
            if opts not in ordered:
                ordered.append(opts)
        if {} not in ordered:
            ordered.append({})
        return ordered or attempts
    return attempts


def _ytdlp_impersonate_targets(*, facebook: bool = False) -> list[str]:
    """Browser targets to try when curl_cffi is available.

    Facebook often needs ``chrome-99`` with logged-in cookies (Tahoe API
    fingerprinting); try that first for FB hosts.
    """
    configured = os.getenv("YTDLP_IMPERSONATE", "").strip()
    if configured:
        return [configured]
    if facebook:
        return ["chrome-99", "chrome", "chrome-110", "safari"]
    return ["chrome", "chrome-110", "safari"]


def _ytdlp_impersonate_attempts(
    *, youtube: bool = False, facebook: bool = False, tiktok: bool = False
) -> list[str | None]:
    """Which ``impersonate`` values to try, in order (``None`` = plain urllib).

    TikTok currently serves a JS-challenge HTML page to curl_cffi fingerprints
    (yt-dlp #17403 / 2026.08.19 extractor). Webpage parse then fails with
    ``Unexpected response from webpage request``. Do not impersonate TikTok.
    """
    if tiktok:
        return [None]
    try:
        import curl_cffi  # noqa: F401
    except ImportError:
        return [None]
    targets: list[str | None] = list(_ytdlp_impersonate_targets(facebook=facebook))
    if youtube:
        targets.append(None)
    return targets


def _open_ytdlp(yt_dlp_mod, opts: dict):
    """Construct YoutubeDL, dropping impersonate if the target is unavailable."""
    try:
        return yt_dlp_mod.YoutubeDL(opts)
    except Exception:
        if "impersonate" not in opts:
            raise
        fallback = {key: value for key, value in opts.items() if key != "impersonate"}
        return yt_dlp_mod.YoutubeDL(fallback)


def _raise_ytdlp_error(exc: BaseException, *, url: str = "") -> None:
    message = _strip_ansi(str(exc).strip() or exc.__class__.__name__)
    lower = message.lower()
    if _COOKIE_DB_HINT_RE.search(message):
        raise RemoteMediaError(
            "브라우저 쿠키 DB를 읽지 못했습니다. Chrome/Edge를 모두 종료한 뒤 "
            "다시 시도하거나, Facebook/TikTok에 로그인한 상태로 해당 영상을 연 다음 "
            "cookies.txt를 내보내 api/.env에 YTDLP_COOKIES_FILE=경로 를 "
            "설정해 주세요."
        ) from exc
    if _is_youtube_bot_check(message):
        raise RemoteMediaError(
            "유튜브가 이 서버를 자동화로 차단했습니다. "
            "공개 영상도 클라우드 IP에서는 막힐 수 있습니다. "
            "영상을 내려받아 파일로 업로드하거나, 같은 네트워크에서 "
            "로그인한 브라우저의 cookies.txt를 YTDLP_COOKIES_FILE로 지정하세요."
        ) from exc
    if _is_cookie_auth_error(message):
        raise RemoteMediaError(
            "이 영상은 로그인·연령/민감 콘텐츠 제한으로 쿠키가 필요합니다. "
            "로그인한 브라우저에서 영상을 연 뒤, "
            "api/.env에 YTDLP_COOKIES_FROM_BROWSER=edge "
            "(또는 chrome)을 설정하고 브라우저를 종료한 상태에서 "
            "서버를 재시작해 주세요. 더 안정적으로는 cookies.txt를 내보내 "
            "YTDLP_COOKIES_FILE=경로 로 지정하세요."
        ) from exc
    if _TIKTOK_EXTRACT_HINT_RE.search(message):
        raise RemoteMediaError(
            "TikTok 영상을 가져오지 못했습니다. "
            "공개 영상이면 잠시 후 다시 시도하거나, 앱에서 영상을 저장해 "
            "파일로 업로드해 주세요. 로그인·연령 제한이면 cookies.txt를 "
            "YTDLP_COOKIES_FILE로 지정하세요."
        ) from exc
    if "ffmpeg is not installed" in lower or (
        "merging of multiple formats" in lower and "ffmpeg" in lower
    ):
        raise RemoteMediaError(
            "영상·음성 병합에 ffmpeg가 필요합니다. "
            "API 서버 이미지에 ffmpeg를 설치한 뒤 재배포해 주세요 "
            "(docker compose up -d --build --force-recreate api)."
        ) from exc
    if "unsupported url" in lower and (
        _is_facebook_share_url(url) or "facebook.com/share/" in lower
    ):
        raise RemoteMediaError(
            "Facebook 공유 링크(share/r)는 로그인 없이는 reel/watch로 "
            "풀리지 않습니다. 브라우저에서 영상을 연 뒤 주소창의 "
            "facebook.com/reel/숫자 또는 watch URL을 붙여 넣으세요. "
            "또는 scripts/export_facebook_cookies.py 로 로그인 쿠키를 내보낸 뒤 "
            "api/.env에 YTDLP_COOKIES_FILE=경로 를 넣고 API를 재시작하세요 "
            "(Windows에서 Edge/Chrome 쿠키 자동 읽기는 자주 실패합니다)."
        ) from exc
    if (
        not _is_youtube_bot_check(message)
        and ("sign in" in lower or "private" in lower or "login" in lower)
    ):
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


def _ffmpeg_location() -> str | None:
    """Directory or binary path for yt-dlp's ``ffmpeg_location`` option."""
    try:
        from .config import get_settings

        configured = (get_settings().ffmpeg_path or "").strip()
    except Exception:  # noqa: BLE001
        configured = ""
    candidate = configured or "ffmpeg"
    path = Path(candidate)
    if path.is_file():
        return str(path.parent if path.parent != Path("") else path)
    # Bare command name — let yt-dlp resolve via PATH when possible.
    which = shutil.which(candidate)
    if which:
        return str(Path(which).parent)
    return None


def _base_ytdlp_opts(
    outtmpl: str,
    max_bytes: int,
    *,
    impersonate: str | None = None,
    youtube_clients: tuple[str, ...] | None = None,
) -> dict:
    opts: dict = {
        "outtmpl": outtmpl,
        # Prefer progressive single-file MP4; fall back to mergeable streams.
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
    ffmpeg_loc = _ffmpeg_location()
    if ffmpeg_loc:
        opts["ffmpeg_location"] = ffmpeg_loc
    node_bin = shutil.which("node")
    if node_bin:
        opts["js_runtimes"] = {"node": {"path": node_bin}}
    if youtube_clients:
        opts["extractor_args"] = {"youtube": {"player_client": list(youtube_clients)}}
    if impersonate:
        opts["impersonate"] = impersonate
    return opts


def download_with_ytdlp(
    url: str,
    dest_dir: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Path:
    """Download a YouTube / Facebook / TikTok page URL via yt-dlp into ``dest_dir``."""
    url = normalize_remote_media_url(url)
    assert_allowed_ytdlp_url(url)
    yt_dlp = _require_yt_dlp()
    dest_dir.mkdir(parents=True, exist_ok=True)
    outtmpl = str(dest_dir / "source.%(ext)s")

    # Prefer browser-safe H.264 progressive MP4 (TikTok's best quality is often
    # HEVC, which many browsers play as audio-only).
    host = _hostname(url)
    facebook_host = bool(host) and _is_facebook_host(host)
    facebook_share = _is_facebook_share_url(url)
    youtube_host = bool(host) and _is_youtube_host(host)
    tiktok_host = bool(host) and _is_tiktok_host(host)
    impersonate_targets = _ytdlp_impersonate_attempts(
        youtube=youtube_host, facebook=facebook_host, tiktok=tiktok_host
    )

    attempt_opts = _ytdlp_attempt_cookie_opts(url)
    client_sets: tuple[tuple[str, ...] | None, ...] = (
        _YOUTUBE_PLAYER_CLIENT_SETS if youtube_host else (None,)
    )

    info = None
    prepared = Path(outtmpl)
    last_error: BaseException | None = None
    cookie_db_failures = 0
    for impersonate in impersonate_targets:
        for youtube_clients in client_sets:
            base_opts = _base_ytdlp_opts(
                outtmpl,
                max_bytes,
                impersonate=impersonate,
                youtube_clients=youtube_clients,
            )
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
                        cookie_db_failures += 1
                        continue
                    if _is_youtube_bot_check(message) or _is_cookie_auth_error(message):
                        continue
                    if _TIKTOK_EXTRACT_HINT_RE.search(message):
                        continue
                    if "unsupported" in lower and "impersonat" in lower:
                        continue
                    if facebook_share and "unsupported url" in lower:
                        continue
                    if facebook_host and "cannot parse data" in lower:
                        continue
                    if cookie_opts and (
                        "assertionerror" in lower or message in {"", "AssertionError"}
                    ):
                        continue
                    _raise_ytdlp_error(exc, url=url)
            if last_error is None and info is not None:
                break
        if last_error is None and info is not None:
            break

    if last_error is not None or info is None:
        if (
            last_error is not None
            and facebook_share
            and cookie_db_failures > 0
            and "unsupported url" in _strip_ansi(str(last_error)).lower()
        ):
            raise RemoteMediaError(
                "Facebook 공유 링크를 열 수 없고, 이 PC의 브라우저 쿠키도 "
                "읽지 못했습니다(Edge/Chrome DPAPI·잠금). "
                "가장 빠른 방법: 브라우저에서 영상을 연 뒤 주소창의 "
                "facebook.com/reel/숫자 URL을 붙여 넣으세요. "
                "또는 scripts/export_facebook_cookies.py 로 쿠키를 만든 뒤 "
                "api/.env에 YTDLP_COOKIES_FILE=경로 를 설정하고 API를 재시작하세요."
            ) from last_error
        if last_error is not None:
            _raise_ytdlp_error(last_error, url=url)
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
