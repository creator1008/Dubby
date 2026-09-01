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
  if (/Android|iPhone|iPad|iPod/i.test(ua)) return true;
  try {
    return (
      navigator.maxTouchPoints > 1 &&
      window.matchMedia("(max-width: 900px)").matches
    );
  } catch {
    return false;
  }
}

function safeFilename(filename: string): string {
  return filename.replace(/[^\w.\-]+/g, "_").slice(-120) || "dubby-output.mp4";
}

async function blobToBase64(blob: Blob): Promise<string> {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function writeBlobToDirectory(
  blob: Blob,
  filename: string,
  directory: Directory,
  subdir: string,
): Promise<string> {
  const safeName = safeFilename(filename);
  const path = `${subdir}/${safeName}`;
  await Filesystem.writeFile({
    path,
    data: await blobToBase64(blob),
    directory,
    recursive: true,
  });
  const { uri } = await Filesystem.getUri({ path, directory });
  return uri;
}

async function shareCachedFile(uri: string): Promise<void> {
  const { value } = await Share.canShare();
  if (!value) return;
  try {
    await Share.share({
      title: "Dubby 더빙 결과",
      text: "Dubby에서 만든 더빙 결과입니다.",
      files: [uri],
      dialogTitle: "갤러리에 저장",
    });
  } catch {
    // Cancelled share sheets should leave the app screen open.
  }
}

/**
 * Native apps: write into the public Movies folder so Gallery indexes it.
 * iOS has no public Movies path — share sheet with Save Video.
 */
async function saveNativeVideoToGallery(
  blob: Blob,
  filename: string,
): Promise<void> {
  if (Capacitor.getPlatform() === "android") {
    try {
      await writeBlobToDirectory(
        blob,
        filename,
        Directory.ExternalStorage,
        "Movies/Dubby",
      );
      return;
    } catch {
      // Scoped storage can reject ExternalStorage; fall through to share.
    }
  }

  const uri = await writeBlobToDirectory(
    blob,
    filename,
    Directory.Cache,
    "dubby",
  );
  await shareCachedFile(uri);
}

/** Save a local Blob; keeps the current SPA/PWA screen open. */
export async function downloadBlobAndShare(
  blob: Blob,
  filename: string,
): Promise<void> {
  const { saveBlobDownload, saveMobileWebVideo } = await import("@/lib/demo-api");
  if (isNativeApp()) {
    await saveNativeVideoToGallery(blob, filename);
    return;
  }
  if (isMobileBrowser()) {
    await saveMobileWebVideo(blob, filename);
    return;
  }
  await saveBlobDownload(blob, filename);
}

/**
 * Download original or dubbed video: pick filename/folder first on desktop.
 * On phones, save into the gallery without opening a player.
 */
export async function downloadProjectOutput(options: {
  filename: string;
  getSignedUrl: () => Promise<string>;
  getBlob: () => Promise<Blob>;
}): Promise<void> {
  const filename = options.filename.trim() || "dubby-output.mp4";

  if (isNativeApp() || isMobileBrowser()) {
    const blob = await options.getBlob();
    await downloadBlobAndShare(blob, filename);
    return;
  }

  const { persistDownloadedBlob, pickSaveFileHandle } = await import(
    "@/lib/demo-api"
  );
  const fileHandle = await pickSaveFileHandle(filename);
  if (fileHandle === "cancelled") return;

  const blob = await options.getBlob();
  await persistDownloadedBlob(blob, filename, fileHandle);
}

export async function downloadAndShare(url: string, filename: string): Promise<void> {
  if (!isNativeApp()) {
    const { forceDownload } = await import("@/lib/demo-api");
    await forceDownload(url, filename);
    return;
  }

  if (Capacitor.getPlatform() === "android") {
    try {
      const result = await Filesystem.downloadFile({
        url,
        path: `Movies/Dubby/${safeFilename(filename)}`,
        directory: Directory.ExternalStorage,
        recursive: true,
      });
      if (result.path) return;
    } catch {
      // Fall through to cache + share.
    }
  }

  const result = await Filesystem.downloadFile({
    url,
    path: `dubby/${safeFilename(filename)}`,
    directory: Directory.Cache,
    recursive: true,
  });
  if (!result.path) throw new Error("다운로드 파일 경로를 확인하지 못했습니다.");
  await shareCachedFile(result.path);
}
