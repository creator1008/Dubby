import { describe, expect, it } from "vitest";
import { isYouTubeMediaUrl, localYoutubeSaveHref } from "@/lib/youtube-url";

describe("isYouTubeMediaUrl", () => {
  it("accepts common YouTube hosts", () => {
    expect(isYouTubeMediaUrl("https://youtu.be/9zOq6_MK40A")).toBe(true);
    expect(isYouTubeMediaUrl("https://www.youtube.com/watch?v=9zOq6_MK40A")).toBe(true);
    expect(isYouTubeMediaUrl("https://m.youtube.com/watch?v=abc")).toBe(true);
  });

  it("rejects direct files and other sites", () => {
    expect(isYouTubeMediaUrl("https://cdn.example.com/clip.mp4")).toBe(false);
    expect(isYouTubeMediaUrl("https://www.facebook.com/watch/?v=1")).toBe(false);
    expect(isYouTubeMediaUrl("not-a-url")).toBe(false);
  });
});

describe("localYoutubeSaveHref", () => {
  it("points at the localhost saver", () => {
    expect(localYoutubeSaveHref("https://youtu.be/9zOq6_MK40A")).toContain(
      "127.0.0.1:8003/save?url=",
    );
  });
});
