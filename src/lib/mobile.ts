"use client";

import { Capacitor } from "@capacitor/core";
import { Directory, Filesystem } from "@capacitor/filesystem";
import { Share } from "@capacitor/share";

export type BillingPlatform = "revenuecat" | "stripe";

export function billingPlatform(platform: string = Capacitor.getPlatform()): BillingPlatform {
  return platform === "ios" || platform === "android" ? "revenuecat" : "stripe";
}

export function isNativeApp(): boolean {
  return billingPlatform() === "revenuecat";
}

export function isMobileBrowser(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent || "";
  if (/Android|iPhone|iPad|iPod|Mobile/i.test(ua)) return true;
  try {
    return (
      navigator.maxTouchPoints > 1 &&
      window.matchMedia("(max-width: 900px)").matches
    );
  } catch {
    return false;
  }
}

function isStandalonePwa(): boolean {
  try {
    if (window.matchMedia("(display-mode: standalone)").matches) return true;
  } catch {
    /* ignore */
  }
  const nav = navigator as Navigator & { standalone?: boolean };
  return nav.standalone === true;
}

function safeFilename(filename: string): string {
  return filename.replace(/[^\w.\-]+/g, "_").slice(-120) || "dubby-output.mp4";
}

/** Save a local Blob; keeps the current SPA/PWA screen open. */
export async function downloadBlobAndShare(
  blob: Blob,
  filename: string,
): Promise<void> {
  const { saveBlobDownload } = await import("@/lib/demo-api");
  if (!isNativeApp()) {
    await saveBlobDownload(blob, filename);
    return;
  }

  const safeName = safeFilename(filename);
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  const base64 = btoa(binary);
  const path = `dubby/${safeName}`;
  await Filesystem.writeFile({
    path,
    data: base64,
    directory: Directory.Cache,
    recursive: true,
  });
  const { uri } = await Filesystem.getUri({
    path,
    directory: Directory.Cache,
  });
  const { value } = await Share.canShare();
  if (value) {
    try {
      await Share.share({
        title: "Dubby 더빙 결과",
        text: "Dubby에서 만든 더빙 결과입니다.",
        files: [uri],
        dialogTitle: "저장 또는 공유",
      });
    } catch {
      // Cancelled share sheets should leave the app screen open.
    }
  }
}

/**
 * Download a finished dub on mobile / desktop / Capacitor.
 *
 * Mobile browsers drop the user-gesture after a long authenticated blob fetch,
 * so iOS/Android often ignore ``<a download>`` and Web Share. Prefer a signed
 * R2 URL with ``Content-Disposition: attachment`` opened under the original tap.
 */
export async function downloadProjectOutput(options: {
  filename: string;
  getSignedUrl: () => Promise<string>;
  getBlob: () => Promise<Blob>;
}): Promise<void> {
  const filename = options.filename.trim() || "dubby-output.mp4";

  if (isNativeApp()) {
    const blob = await options.getBlob();
    await downloadBlobAndShare(blob, filename);
    return;
  }

  if (isMobileBrowser()) {
    const popup =
      !isStandalonePwa() && typeof window.open === "function"
        ? window.open("about:blank", "_blank")
        : null;
    try {
      const url = await options.getSignedUrl();
      if (popup && !popup.closed) {
        popup.location.replace(url);
        return;
      }
      // PWA / popup blocked: same-tab navigation still honors attachment.
      window.location.assign(url);
      return;
    } catch (err) {
      popup?.close();
      // Fall through — tunnel signed-url path may be down; try blob.
      try {
        const blob = await options.getBlob();
        await downloadBlobAndShare(blob, filename);
        return;
      } catch {
        throw err instanceof Error
          ? err
          : new Error("다운로드하지 못했습니다.");
      }
    }
  }

  const blob = await options.getBlob();
  await downloadBlobAndShare(blob, filename);
}

export async function downloadAndShare(url: string, filename: string): Promise<void> {
  if (!isNativeApp()) {
    const { forceDownload } = await import("@/lib/demo-api");
    await forceDownload(url, filename);
    return;
  }

  const result = await Filesystem.downloadFile({
    url,
    path: `dubby/${safeFilename(filename)}`,
    directory: Directory.Cache,
    recursive: true,
  });
  if (!result.path) throw new Error("다운로드 파일 경로를 확인하지 못했습니다.");

  const { value } = await Share.canShare();
  if (value) {
    try {
      await Share.share({
        title: "Dubby 더빙 결과",
        text: "Dubby에서 만든 더빙 결과입니다.",
        files: [result.path],
        dialogTitle: "저장 또는 공유",
      });
    } catch {
      // Cancelled share sheets should leave the app screen open.
    }
  }
}
