"use client";

import { SiteHeader } from "./SiteHeader";
import { Hero } from "./Hero";
import { HowItWorks, LangsBand, SiteFooter } from "./LandingSections";
import { WaitlistForm } from "./WaitlistForm";
import { useLocale } from "@/lib/i18n/locale-context";

function LocalizedLandingPage() {
  const { dict } = useLocale();

  return (
    <div className="page-shell">
      <title>{dict.pageTitle}</title>
      <SiteHeader />
      <main>
        <Hero />
        <HowItWorks />
        <LangsBand />
        <WaitlistForm />
      </main>
      <SiteFooter />
    </div>
  );
}

export function LandingPage() {
  return <LocalizedLandingPage />;
}
