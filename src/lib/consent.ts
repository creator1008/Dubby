"use client";

import { useCallback, useSyncExternalStore } from "react";

const VOICE_CONSENT_KEY = "dubby.voice-consent.v1";
const VOICE_CONSENT_EVENT = "dubby-voice-consent-changed";

type StoredConsent = {
  accepted: true;
  acceptedAt: string;
  policyVersion: "2026-07-17";
};

export function useVoiceConsent() {
  const accepted = useSyncExternalStore(
    (notify) => {
      window.addEventListener("storage", notify);
      window.addEventListener(VOICE_CONSENT_EVENT, notify);
      return () => {
        window.removeEventListener("storage", notify);
        window.removeEventListener(VOICE_CONSENT_EVENT, notify);
      };
    },
    () => {
      try {
        const stored = JSON.parse(localStorage.getItem(VOICE_CONSENT_KEY) ?? "null") as StoredConsent | null;
        return stored?.accepted === true;
      } catch {
        return false;
      }
    },
    () => false,
  );

  const setAccepted = useCallback((next: boolean) => {
    try {
      if (next) {
        const consent: StoredConsent = {
          accepted: true,
          acceptedAt: new Date().toISOString(),
          policyVersion: "2026-07-17",
        };
        localStorage.setItem(VOICE_CONSENT_KEY, JSON.stringify(consent));
      } else {
        localStorage.removeItem(VOICE_CONSENT_KEY);
      }
    } finally {
      window.dispatchEvent(new Event(VOICE_CONSENT_EVENT));
    }
  }, []);

  return { accepted, setAccepted };
}
