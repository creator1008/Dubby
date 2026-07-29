"use client";

import { useCallback, useEffect, useState } from "react";
import { withBasePath } from "@/lib/base-path";

const INSTALLED_KEY = "dubby.pwaInstalled";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

function isStandaloneDisplay(): boolean {
  if (typeof window === "undefined") return false;
  const media = window.matchMedia("(display-mode: standalone)").matches;
  const iosStandalone =
    "standalone" in window.navigator &&
    Boolean((window.navigator as Navigator & { standalone?: boolean }).standalone);
  return media || iosStandalone;
}

function alreadyInstalled(): boolean {
  if (typeof window === "undefined") return true;
  if (isStandaloneDisplay()) return true;
  try {
    return window.localStorage.getItem(INSTALLED_KEY) === "1";
  } catch {
    return false;
  }
}

function markInstalled() {
  try {
    window.localStorage.setItem(INSTALLED_KEY, "1");
  } catch {
    /* ignore */
  }
}

export function registerDubbyServiceWorker() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  const swUrl = withBasePath("/sw.js");
  void navigator.serviceWorker.register(swUrl, { scope: withBasePath("/") }).catch(() => {
    /* SW optional on some hosts */
  });
}

type PwaInstallPromptProps = {
  /** When true, open the confirm dialog (e.g. after clicking the header link). */
  openRequestId: number;
  onAvailabilityChange?: (available: boolean) => void;
};

export function PwaInstallPrompt({
  openRequestId,
  onAvailabilityChange,
}: PwaInstallPromptProps) {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [iosHint, setIosHint] = useState(false);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    registerDubbyServiceWorker();
    const installedNow = alreadyInstalled();
    setInstalled(installedNow);
    onAvailabilityChange?.(!installedNow);

    const onBeforeInstall = (event: Event) => {
      event.preventDefault();
      setDeferred(event as BeforeInstallPromptEvent);
    };
    const onAppInstalled = () => {
      markInstalled();
      setInstalled(true);
      setOpen(false);
      setDeferred(null);
      onAvailabilityChange?.(false);
    };

    window.addEventListener("beforeinstallprompt", onBeforeInstall);
    window.addEventListener("appinstalled", onAppInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onBeforeInstall);
      window.removeEventListener("appinstalled", onAppInstalled);
    };
  }, [onAvailabilityChange]);

  useEffect(() => {
    if (!openRequestId || installed) return;
    const isIos =
      /iphone|ipad|ipod/i.test(window.navigator.userAgent) ||
      (window.navigator.platform === "MacIntel" && window.navigator.maxTouchPoints > 1);
    setIosHint(isIos && !deferred);
    setOpen(true);
  }, [openRequestId, installed, deferred]);

  const close = useCallback(() => {
    setOpen(false);
  }, []);

  const confirmInstall = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    try {
      if (deferred) {
        await deferred.prompt();
        const choice = await deferred.userChoice;
        if (choice.outcome === "accepted") {
          markInstalled();
          setInstalled(true);
          onAvailabilityChange?.(false);
        }
        setDeferred(null);
        setOpen(false);
        return;
      }
      // iOS / browsers without beforeinstallprompt: keep hint visible until user closes.
      if (iosHint) return;
      setOpen(false);
    } finally {
      setBusy(false);
    }
  }, [busy, deferred, iosHint, onAvailabilityChange]);

  if (installed || !open) return null;

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
          {iosHint
            ? "Safari 공유 버튼(□↑)을 누른 뒤 「홈 화면에 추가」를 선택하세요."
            : "홈 화면에 Dubby 아이콘을 만들어 앱처럼 바로 실행할 수 있습니다."}
        </p>
        <div className="pwa-install-actions">
          <button type="button" className="btn-ghost" onClick={close} disabled={busy}>
            아니오
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => void confirmInstall()}
            disabled={busy}
          >
            {iosHint ? "확인" : "추가"}
          </button>
        </div>
      </div>
    </div>
  );
}
