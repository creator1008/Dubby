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

function safeFilename(filename: string): string {
  return filename.replace(/[^\w.\-]+/g, "_").slice(-120) || "dubby-output.mp4";
}

export async function downloadAndShare(url: string, filename: string): Promise<void> {
  if (!isNativeApp()) {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    anchor.rel = "noopener";
    anchor.click();
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
    await Share.share({
      title: "Dubby 더빙 결과",
      text: "Dubby에서 만든 더빙 결과입니다.",
      files: [result.path],
      dialogTitle: "저장 또는 공유",
    });
  }
}
