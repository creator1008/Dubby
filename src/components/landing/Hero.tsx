"use client";

import { withBasePath } from "@/lib/base-path";
import { useLocale } from "@/lib/i18n/locale-context";
import { BeforeAfterPlayer } from "./BeforeAfterPlayer";

export function Hero() {
  const { dict } = useLocale();

  return (
    <section className="hero hero-v2" id="top">
      <div className="hero-stage">
        <div className="hero-copy hero-copy-v2">
          <p className="hero-brand">{dict.brand}</p>
          <h1 className="hero-tagline">{dict.tagline}</h1>
        </div>
        <div className="hero-demo" id="demo">
          <BeforeAfterPlayer
            beforeSrc={withBasePath("/demo-before.mp4")}
            afterSrc={withBasePath("/demo-after.mp4")}
            beforeLabel={dict.before}
            afterLabel={dict.after}
            listenBeforeLabel={dict.listenBefore}
            listenAfterLabel={dict.listenAfter}
            pauseLabel={dict.pause}
          />
        </div>
      </div>
    </section>
  );
}
