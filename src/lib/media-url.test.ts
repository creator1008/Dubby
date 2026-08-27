import { describe, expect, it } from "vitest";
import {
  preferStableMediaUrl,
  signedUrlExpiryMs,
  signedUrlIsFresh,
} from "@/lib/media-url";

function amzUrl(dateStamp: string, expiresSec: number): string {
  return (
    "https://example.r2.cloudflarestorage.com/dubby/users/u/source/video.mp4" +
    `?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Date=${dateStamp}` +
    `&X-Amz-Expires=${expiresSec}&X-Amz-Signature=abc`
  );
}

describe("signed R2 media URLs", () => {
  it("parses AWS expiry from query params", () => {
    const expiry = signedUrlExpiryMs(amzUrl("20260827T080000Z", 300));
    expect(expiry).toBe(Date.UTC(2026, 7, 27, 8, 0, 0) + 300_000);
  });

  it("keeps a fresh signature during poll remounts", () => {
    const now = new Date();
    const stamp = now.toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z");
    const current = amzUrl(stamp, 3600);
    const next = amzUrl(stamp, 3600).replace("abc", "def");
    expect(preferStableMediaUrl(current, next)).toBe(current);
  });

  it("replaces an expired signature so playback can resume", () => {
    const expired = amzUrl("20200101T000000Z", 300);
    const fresh = amzUrl("20990101T000000Z", 3600);
    expect(signedUrlIsFresh(expired)).toBe(false);
    expect(preferStableMediaUrl(expired, fresh)).toBe(fresh);
  });
});
