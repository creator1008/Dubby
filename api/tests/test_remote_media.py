"""Unit tests for remote media URL classification and SSRF helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.remote_media import (
    RemoteMediaError,
    assert_public_http_url,
    assert_safe_direct_media_url,
    classify_media_url,
    is_ytdlp_platform_host,
    normalize_remote_media_url,
    _is_cookie_auth_error,
    _is_facebook_share_url,
    _is_youtube_bot_check,
    _raise_ytdlp_error,
    _strip_ansi,
    _ytdlp_attempt_cookie_opts,
    _ytdlp_cookie_option_sets,
    _ytdlp_impersonate_targets,
)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ytdlp"),
        ("https://youtu.be/dQw4w9WgXcQ", "ytdlp"),
        ("https://m.youtube.com/watch?v=abc", "ytdlp"),
        ("https://www.facebook.com/watch/?v=123", "ytdlp"),
        ("https://www.facebook.com/share/r/1JhDTfb53T/?_fb_noscript=1", "ytdlp"),
        ("https://fb.watch/abc123/", "ytdlp"),
        ("https://www.tiktok.com/@user/video/123", "ytdlp"),
        ("https://vm.tiktok.com/ZMabcdef/", "ytdlp"),
        ("https://vt.tiktok.com/ZSXbHMHEE", "ytdlp"),
        ("https://cdn.example.com/clip.mp4", "direct"),
        ("https://cdn.example.com/path/video.webm?token=1", "direct"),
        ("https://cdn.example.com/path/movie.MOV", "direct"),
        ("https://example.com/watch?v=1", "unsupported"),
        ("ftp://cdn.example.com/clip.mp4", "unsupported"),
        ("not-a-url", "unsupported"),
        ("", "unsupported"),
    ],
)
def test_classify_media_url(url: str, expected: str) -> None:
    assert classify_media_url(url) == expected


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("youtube.com", True),
        ("www.youtube.com", True),
        ("m.youtube.com", True),
        ("youtu.be", True),
        ("facebook.com", True),
        ("www.facebook.com", True),
        ("fb.watch", True),
        ("tiktok.com", True),
        ("www.tiktok.com", True),
        ("vm.tiktok.com", True),
        ("vt.tiktok.com", True),
        ("notyoutube.com", False),
        ("evil-youtube.com", False),
        ("example.com", False),
    ],
)
def test_is_ytdlp_platform_host(host: str, expected: bool) -> None:
    assert is_ytdlp_platform_host(host) is expected


def test_assert_safe_direct_rejects_platform_urls() -> None:
    with pytest.raises(RemoteMediaError):
        assert_safe_direct_media_url("https://www.youtube.com/watch?v=abc")


def test_assert_public_http_url_rejects_localhost() -> None:
    with pytest.raises(RemoteMediaError):
        assert_public_http_url("http://localhost/video.mp4")
    with pytest.raises(RemoteMediaError):
        assert_public_http_url("http://127.0.0.1/video.mp4")
    with pytest.raises(RemoteMediaError):
        assert_public_http_url("http://10.0.0.8/video.mp4")


def test_assert_public_http_url_rejects_private_resolved_host() -> None:
    def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ARG001
        return [(2, 0, 0, "", ("10.0.0.8", 0))]

    with patch("app.remote_media.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        with pytest.raises(RemoteMediaError):
            assert_public_http_url("https://internal.example/video.mp4")


def test_assert_public_http_url_allows_nat64_resolved_host() -> None:
    """TikTok short hosts often resolve to DNS64/NAT64 (reserved but global)."""

    def fake_getaddrinfo(host, port, *args, **kwargs):  # noqa: ANN001, ARG001
        return [
            (10, 0, 0, "", ("64:ff9b::1743:3599", 0, 0, 0)),
            (2, 0, 0, "", ("23.59.72.74", 0)),
        ]

    with patch("app.remote_media.socket.getaddrinfo", side_effect=fake_getaddrinfo):
        assert_public_http_url("https://vt.tiktok.com/ZSXbHMHEE")


def test_strip_ansi_removes_color_codes() -> None:
    raw = "\x1b[0;31mERROR:\x1b[0m [TikTok] login required"
    assert _strip_ansi(raw) == "ERROR: [TikTok] login required"


def test_is_cookie_auth_error_detects_tiktok_gate() -> None:
    message = (
        "ERROR: [TikTok] 123: This post may not be comfortable for some audiences. "
        "Log in for access. Use --cookies-from-browser"
    )
    assert _is_cookie_auth_error(message) is True
    assert _is_cookie_auth_error("HTTP Error 404: Not Found") is False


def test_youtube_bot_check_is_not_age_cookie_error() -> None:
    message = (
        "ERROR: [youtube] dQw4w9WgXcQ: Sign in to confirm you’re not a bot. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )
    assert _is_youtube_bot_check(message) is True
    assert _is_cookie_auth_error(message) is False
    with pytest.raises(RemoteMediaError, match="자동화로 차단") as exc_info:
        _raise_ytdlp_error(Exception(message), url="https://www.youtube.com/watch?v=dQw4w9WgXcQ")
    assert "쿠키가 필요합니다" not in str(exc_info.value)


def test_age_restricted_youtube_still_asks_for_cookies() -> None:
    message = "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate."
    assert _is_cookie_auth_error(message) is True
    assert _is_youtube_bot_check(message) is False
    with pytest.raises(RemoteMediaError, match="쿠키가 필요합니다"):
        _raise_ytdlp_error(Exception(message), url="https://www.youtube.com/watch?v=abc")


def test_ytdlp_cookie_option_sets_includes_explicit_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("YTDLP_COOKIES_AUTO_BROWSER", "0")
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "chrome")
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    options = _ytdlp_cookie_option_sets()
    assert options == [{"cookiesfrombrowser": ("chrome", None, None, None)}]


def test_normalize_strips_facebook_noscript() -> None:
    cleaned = normalize_remote_media_url(
        "https://www.facebook.com/share/r/1JhDTfb53T/?_fb_noscript=1&mibextid=abc"
    )
    assert cleaned == "https://www.facebook.com/share/r/1JhDTfb53T/?mibextid=abc"
    assert _is_facebook_share_url(cleaned)


def test_facebook_cookie_attempts_prefer_browser(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("YTDLP_COOKIES_AUTO_BROWSER", "0")
    monkeypatch.setenv("YTDLP_COOKIES_FROM_BROWSER", "edge")
    monkeypatch.delenv("YTDLP_COOKIES_FILE", raising=False)
    attempts = _ytdlp_attempt_cookie_opts(
        "https://www.facebook.com/share/r/1JhDTfb53T/"
    )
    assert attempts[0] == {"cookiesfrombrowser": ("edge", None, None, None)}
    assert attempts[-1] == {}


def test_facebook_impersonate_prefers_chrome_99(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("YTDLP_IMPERSONATE", raising=False)
    assert _ytdlp_impersonate_targets(facebook=True)[0] == "chrome-99"
    assert _ytdlp_impersonate_targets(facebook=False)[0] == "chrome"
