"use client";

import { useCallback, useState } from "react";
import { SiteHeader } from "./SiteHeader";
import { Hero } from "./Hero";
import { HowItWorks, LangsBand, SiteFooter } from "./LandingSections";
import { WaitlistForm } from "./WaitlistForm";
import { useLocale } from "@/lib/i18n/locale-context";
import { PwaInstallPrompt } from "@/components/pwa/PwaInstallPrompt";

function LocalizedLandingPage() {
  const { dict } = useLocale();
  const [installRequestId, setInstallRequestId] = useState(0);
  const [installAvailable, setInstallAvailable] = useState(true);

  const requestInstall = useCallback(() => {
    setInstallRequestId((id) => id + 1);
  }, []);

  return (
    <div className="page-shell">
      <title>{dict.pageTitle}</title>
      <SiteHeader
        showInstallLink={installAvailable}
        onInstallClick={requestInstall}
      />
      <main>
        <Hero />
        <HowItWorks />
        <LangsBand />
        <WaitlistForm />
      </main>
      <SiteFooter />
      <PwaInstallPrompt
        openRequestId={installRequestId}
        onAvailabilityChange={setInstallAvailable}
      />
    </div>
  );
}

export function LandingPage() {
  return <LocalizedLandingPage />;
}
