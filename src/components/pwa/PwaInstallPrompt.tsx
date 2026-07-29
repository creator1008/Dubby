"use client";

import { useCallback, useEffect, useState } from "react";
import { withBasePath } from "@/lib/base-path";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

/** Survives React remounts — beforeinstallprompt only fires once. */
let deferredInstallPrompt: BeforeInstallPromptEvent | null = null;
let listeningForInstall = false;

function ensureInstallListeners() {
  if (typeof window === "undefined" || listeningForInstall) return;
  listeningForInstall = true;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    deferredInstallPrompt = event as BeforeInstallPromptEvent;
    window.dispatchEvent(new Event("dubby-pwa-prompt-ready"));
  });
  window.addEventListener("appinstalled", () => {
    deferredInstallPrompt = null;
    window.dispatchEvent(new Event("dubby-pwa-installed"));
  });
}

export function isPwaStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const media =
    window.matchMedia("(display-mode: standalone)").matches ||
    window.matchMedia("(display-mode: fullscreen)").matches ||
    window.matchMedia("(display-mode: minimal-ui)").matches;
  const iosStandalone =
    "standalone" in window.navigator &&
    Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);
  return media || iosStandalone;
}

/** Hide install CTA when already running as an installed home-screen app. */
export function shouldOfferPwaInstall(): boolean {
  return !isPwaStandalone();
}

export function registerDubbyServiceWorker() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  ensureInstallListeners();
  const swUrl = withBasePath("/sw.js");
  const scope = withBasePath("/");
  void navigator.serviceWorker.register(swUrl, { scope }).catch((err) => {
    console.warn("Dubby SW register failed", err);
  });
}

type PwaInstallPromptProps = {
  openRequestId: number;
  onAvailabilityChange?: (available: boolean) => void;
};

export function PwaInstallPrompt({
  openRequestId,
  onAvailabilityChange,
}: PwaInstallPromptProps) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [manualHint, setManualHint] = useState(false);
  const [hasDeferred, setHasDeferred] = useState(false);
  const [standalone, setStandalone] = useState(false);

  const syncAvailability = useCallback(() => {
    const installed = isPwaStandalone();
    setStandalone(installed);
    setHasDeferred(Boolean(deferredInstallPrompt));
    onAvailabilityChange?.(!installed);
  }, [onAvailabilityChange]);

  useEffect(() => {
    registerDubbyServiceWorker();
    syncAvailability();
    const onReady = () => syncAvailability();
    const onInstalled = () => {
      deferredInstallPrompt = null;
      syncAvailability();
      setOpen(false);
    };
    window.addEventListener("dubby-pwa-prompt-ready", onReady);
    window.addEventListener("dubby-pwa-installed", onInstalled);
    const media = window.matchMedia("(display-mode: standalone)");
    const onMedia = () => syncAvailability();
    media.addEventListener?.("change", onMedia);
    return () => {
      window.removeEventListener("dubby-pwa-prompt-ready", onReady);
      window.removeEventListener("dubby-pwa-installed", onInstalled);
      media.removeEventListener?.("change", onMedia);
    };
  }, [syncAvailability]);

  useEffect(() => {
    if (!openRequestId || standalone) return;
    const isIos =
      /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
      (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);
    setManualHint(isIos || !deferredInstallPrompt);
    setHasDeferred(Boolean(deferredInstallPrompt));
    setOpen(true);
  }, [openRequestId, standalone]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const confirmInstall = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      const promptEvent = deferredInstallPrompt;
      if (promptEvent) {
        await promptEvent.prompt();
        const choice = await promptEvent.userChoice;
        deferredInstallPrompt = null;
        setHasDeferred(false);
        if (choice.outcome === "accepted") {
          // Prefer appinstalled / standalone detection over optimistic hide.
          setOpen(false);
          window.setTimeout(() => syncAvailability(), 500);
          return;
        }
        setOpen(false);
        return;
      }
      // No native prompt (or not ready yet): keep manual instructions visible.
      setManualHint(true);
    } finally {
      setBusy(false);
    }
  }, [busy, syncAvailability]);

  if (standalone || !open) return null;

  const isIos =
    typeof navigator !== "undefined" &&
    (/iphone|ipad|ipod/i.test(navigator.userAgent) ||
      (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1));

  return (
    <div className="pwa-install-backdrop" role="presentation" onClick={close}>
      <div
        className="pwa-install-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="pwa-install-title"
        onClick={(event) => event.stopPropagation()}
      >
        <img
          className="pwa-install-icon"
          src={withBasePath("/icons/icon-192.png")}
          width={72}
          height={72}
          alt=""
        />
        <h2 id="pwa-install-title">홈 화면에 아이콘을 추가하시겠습니까?</h2>
        <p className="pwa-install-body">
          {isIos
            ? "Safari 하단 공유(□↑) → 「홈 화면에 추가」를 눌러 주세요."
            : hasDeferred && !manualHint
              ? "홈 화면에 Dubby 아이콘을 만들어 앱처럼 바로 실행할 수 있습니다."
              : "Chrome 메뉴(⋮) → 「홈 화면에 추가」또는 「앱 설치」를 선택해 주세요. 설치 후에는 이 버튼이 사라집니다."}
        </p>
        <div className="pwa-install-actions">
          <button type="button" className="btn-ghost" onClick={close} disabled={busy}>
            닫기
          </button>
          {hasDeferred ? (
            <button
              type="button"
              className="btn-primary"
              onClick={() => void confirmInstall()}
              disabled={busy}
            >
              추가
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={close} disabled={busy}>
              확인
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
