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

const YT_DLP_PY_CANDIDATES = [
  "D:\\Coding\\Dubby\\api\\.venv\\Scripts\\python.exe",
  "%LOCALAPPDATA%\\Programs\\Python\\Python314\\python.exe",
];

/** One-shot Windows script: save the YouTube file to the Downloads folder. */
export function youtubeSaveCmd(url: string): string {
  const safeUrl = url.trim().replace(/"/g, "");
  const findPy = YT_DLP_PY_CANDIDATES.map(
    (path) => `if exist "${path}" set PY=${path}`,
  ).join("\r\n");
  return [
    "@echo off",
    "chcp 65001 >nul",
    "set PY=",
    findPy,
    'if not defined PY where python >nul 2>&1 && for /f "delims=" %%P in (\'where python\') do if not defined PY set PY=%%P',
    "if not defined PY (",
    "  echo Python 을 찾지 못했습니다. Dubby 폴더의 start-youtube-save.bat 을 실행한 뒤 다시 시도하세요.",
    "  pause",
    "  exit /b 1",
    ")",
    "echo 유튜브 영상을 다운로드 폴더에 저장합니다...",
    `\"%PY%\" -m yt_dlp --js-runtimes node -f "b[ext=mp4]/bv*+ba/b" --merge-output-format mp4 --extractor-args "youtube:player_client=tv,android,ios" --impersonate chrome -o "%USERPROFILE%\\Downloads\\%%(title)s [%%(id)s].%%(ext)s" --no-playlist "${safeUrl}"`,
    "if errorlevel 1 (",
    "  echo 저장에 실패했습니다.",
    "  pause",
    "  exit /b 1",
    ")",
    "echo 저장이 끝났습니다. Dubby에서 이 파일을 올리세요.",
    'explorer "%USERPROFILE%\\Downloads"',
    "pause",
    "",
  ].join("\r\n");
}

export function downloadYoutubeSaveCmd(url: string): void {
  const blob = new Blob([youtubeSaveCmd(url)], { type: "application/octet-stream" });
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = "dubby-유튜브-저장.cmd";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 2_000);
}
