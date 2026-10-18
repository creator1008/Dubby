/** True when the URL is a YouTube watch/share/short host (not a direct MP4). */
export function isYouTubeMediaUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  try {
    const host = new URL(trimmed).hostname.replace(/\.$/, "").toLowerCase();
    return host === "youtu.be" || host === "youtube.com" || host.endsWith(".youtube.com");
  } catch {
    return false;
  }
}

export const LOCAL_YOUTUBE_SAVE_ORIGIN = "http://127.0.0.1:8003";

export function localYoutubeSaveHref(url: string): string {
  return `${LOCAL_YOUTUBE_SAVE_ORIGIN}/save?url=${encodeURIComponent(url.trim())}`;
}
