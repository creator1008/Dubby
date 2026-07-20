"use client";

import { useLocale } from "@/lib/i18n/locale-context";

export function HowItWorks() {
  const { dict } = useLocale();

  return (
    <section className="section how" id="how">
      <div className="section-head">
        <h2>{dict.howTitle}</h2>
        <p>{dict.howSupport}</p>
      </div>
      <ol className="steps">
        {dict.steps.map((step, i) => (
          <li key={step.title}>
            <span className="step-num">{String(i + 1).padStart(2, "0")}</span>
            <h3>{step.title}</h3>
            <p>{step.body}</p>
          </li>
        ))}
      </ol>
    </section>
  );
}

export function LangsBand() {
  const { dict } = useLocale();

  return (
    <section className="section langs">
      <h2>{dict.langsTitle}</h2>
      <p>{dict.langsBody}</p>
      <div className="lang-pills" aria-hidden>
        <span>EN</span>
        <span>KO</span>
        <span>VI</span>
      </div>
    </section>
  );
}

export function SiteFooter() {
  const { dict } = useLocale();

  return <footer className="site-footer">{dict.footer}</footer>;
}
