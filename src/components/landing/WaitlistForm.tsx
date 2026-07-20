"use client";

import { useState, type FormEvent } from "react";
import { useLocale } from "@/lib/i18n/locale-context";

export function WaitlistForm() {
  const { dict, locale } = useLocale();
  const [email, setEmail] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "ok" | "err">(
    "idle",
  );

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setStatus("loading");
    try {
      const response = await fetch("/api/waitlist", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: email.trim(), locale }),
      });
      if (!response.ok) throw new Error("waitlist_failed");
      setStatus("ok");
      setEmail("");
    } catch {
      setStatus("err");
    }
  };

  return (
    <section className="waitlist" id="waitlist">
      <div className="waitlist-inner">
        <h2>{dict.waitlistTitle}</h2>
        <p>{dict.waitlistHint}</p>
        <form className="waitlist-form" onSubmit={onSubmit}>
          <input
            type="email"
            required
            autoComplete="email"
            placeholder={dict.emailPlaceholder}
            value={email}
            onChange={(e) => {
              setEmail(e.target.value);
              if (status !== "idle") setStatus("idle");
            }}
          />
          <button type="submit" disabled={status === "loading"}>
            {status === "loading" ? "…" : dict.waitlistSubmit}
          </button>
        </form>
        {status === "ok" && (
          <p className="form-msg ok" role="status">
            {dict.waitlistSuccess}
          </p>
        )}
        {status === "err" && (
          <p className="form-msg err" role="alert">
            {dict.waitlistError}
          </p>
        )}
      </div>
    </section>
  );
}
