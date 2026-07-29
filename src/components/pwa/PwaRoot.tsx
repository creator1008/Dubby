"use client";

import { useEffect } from "react";
import { registerDubbyServiceWorker } from "@/components/pwa/PwaInstallPrompt";

/** Registers the service worker site-wide (home + app routes). */
export function PwaRegistrar() {
  useEffect(() => {
    registerDubbyServiceWorker();
  }, []);
  return null;
}
