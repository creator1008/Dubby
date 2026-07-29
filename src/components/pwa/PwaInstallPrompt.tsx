"use client";

import { useCallback, useEffect, useState } from "react";
import { withBasePath } from "@/lib/base-path";
import { useLocale } from "@/lib/i18n/locale-context";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

declare global {
  interface Window {
    __dubbyDeferredPrompt?: BeforeInstallPromptEvent | null;
  }
}

function getDeferred(): BeforeInstallPromptEvent | null {
  if (typeof window === "undefined") return null;
  return window.__dubbyDeferredPrompt ?? null;
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

export function shouldOfferPwaInstall(): boolean {
  return !isPwaStandalone();
}

export function registerDubbyServiceWorker() {
  if (typeof window === "undefined" || !("serviceWorker" in navigator)) return;
  const swUrl = withBasePath("/sw.js");
  const scope = withBasePath("/");
  void navigator.serviceWorker
    .register(swUrl, { scope })
    .then((reg) => reg.update())
    .catch((err) => console.warn("Dubby SW register failed", err));
}

type PwaInstallPromptProps = {
  openRequestId: number;
  onAvailabilityChange?: (available: boolean) => void;
};

export function PwaInstallPrompt({
  openRequestId,
  onAvailabilityChange,
}: PwaInstallPromptProps) {
  const { dict } = useLocale();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [hasDeferred, setHasDeferred] = useState(false);
  const [standalone, setStandalone] = useState(false);

  const syncAvailability = useCallback(() => {
    const installed = isPwaStandalone();
    setStandalone(installed);
    setHasDeferred(Boolean(getDeferred()));
    onAvailabilityChange?.(!installed);
  }, [onAvailabilityChange]);

  useEffect(() => {
    registerDubbyServiceWorker();
    syncAvailability();
    const onReady = () => syncAvailability();
    const onInstalled = () => {
      window.__dubbyDeferredPrompt = null;
      syncAvailability();
      setOpen(false);
    };
    window.addEventListener("dubby-pwa-prompt-ready", onReady);
    window.addEventListener("dubby-pwa-installed", onInstalled);
    window.addEventListener("appinstalled", onInstalled);
    const media = window.matchMedia("(display-mode: standalone)");
    media.addEventListener?.("change", onReady);
    return () => {
      window.removeEventListener("dubby-pwa-prompt-ready", onReady);
      window.removeEventListener("dubby-pwa-installed", onInstalled);
      window.removeEventListener("appinstalled", onInstalled);
      media.removeEventListener?.("change", onReady);
    };
  }, [syncAvailability]);

  useEffect(() => {
    if (!openRequestId || standalone) return;
    setHasDeferred(Boolean(getDeferred()));
    setOpen(true);
  }, [openRequestId, standalone]);

  const close = useCallback(() => setOpen(false), []);

  const confirmInstall = useCallback(async () => {
    if (busy) return;
    const promptEvent = getDeferred();
    if (!promptEvent) {
      setHasDeferred(false);
      return;
    }
    setBusy(true);
    try {
      await promptEvent.prompt();
      await promptEvent.userChoice;
      window.__dubbyDeferredPrompt = null;
      setHasDeferred(false);
      setOpen(false);
      window.setTimeout(() => syncAvailability(), 400);
    } finally {
      setBusy(false);
    }
  }, [busy, syncAvailability]);

  if (standalone || !open) return null;

  const isIos =
    /iphone|ipad|ipod/i.test(navigator.userAgent) ||
    (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

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
        <h2 id="pwa-install-title">{dict.addToHomeConfirm}</h2>
        <p className="pwa-install-body">
          {isIos
            ? dict.addToHomeIos
            : hasDeferred
              ? dict.addToHomeBody
              : dict.addToHomeManual}
        </p>
        <div className="pwa-install-actions">
          <button type="button" className="btn-ghost" onClick={close} disabled={busy}>
            {dict.addToHomeClose}
          </button>
          {hasDeferred ? (
            <button
              type="button"
              className="btn-primary"
              onClick={() => void confirmInstall()}
              disabled={busy}
            >
              {dict.addToHomeAdd}
            </button>
          ) : (
            <button type="button" className="btn-primary" onClick={close} disabled={busy}>
              {dict.addToHomeOk}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
