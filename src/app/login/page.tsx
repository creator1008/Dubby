"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import type { Provider } from "@supabase/supabase-js";
import { getSupabase } from "@/lib/supabase";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";

const PROVIDERS: Array<{
  id: Extract<Provider, "google" | "facebook" | "kakao">;
  className: string;
}> = [
  { id: "google", className: "oauth-google" },
  { id: "facebook", className: "oauth-facebook" },
  { id: "kakao", className: "oauth-kakao" },
];

const COUNTRIES = [
  ["KR", "대한민국"],
  ["US", "United States"],
  ["VN", "Việt Nam"],
  ["JP", "日本"],
  ["CN", "中国"],
  ["OTHER", "기타 / Other"],
];

export default function LoginPage() {
  const supabase = getSupabase();
  const text = useAppDictionary();
  const [country, setCountry] = useState("KR");
  const [busy, setBusy] = useState<Provider | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supabase) return;
    void supabase.auth.getSession().then(({ data }) => {
      if (data.session) window.location.replace("/app");
    });
  }, [supabase]);

  const signIn = async (provider: Extract<Provider, "google" | "facebook" | "kakao">) => {
    if (!supabase) {
      setError(text.supabaseRequired);
      return;
    }
    setBusy(provider);
    setError(null);
    window.sessionStorage.setItem("dubby-signup-country", country);
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: `${window.location.origin}/auth/callback/`,
      },
    });
    if (oauthError) {
      setError(oauthError.message);
      setBusy(null);
    }
  };

  return (
    <main className="auth-page">
      <header className="auth-header">
        <Link href="/" className="brand-mark">Dubby</Link>
        <LanguageSwitcher />
      </header>
      <section className="auth-card">
        <div>
          <p className="eyebrow">ACCOUNT</p>
          <h1>{text.loginTitle}</h1>
          <p className="muted">{text.loginDescription}</p>
        </div>
        <label>
          {text.country}
          <select value={country} onChange={(event) => setCountry(event.target.value)}>
            {COUNTRIES.map(([code, name]) => (
              <option value={code} key={code}>{name}</option>
            ))}
          </select>
        </label>
        <div className="oauth-list">
          {PROVIDERS.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={`oauth-button ${provider.className}`}
              disabled={Boolean(busy)}
              onClick={() => void signIn(provider.id)}
            >
              {busy === provider.id
                ? text.connecting
                : text[
                    provider.id === "google"
                      ? "continueGoogle"
                      : provider.id === "facebook"
                        ? "continueFacebook"
                        : "continueKakao"
                  ]}
            </button>
          ))}
        </div>
        {error && <p className="form-msg err">{error}</p>}
        <p className="auth-terms muted">
          {text.loginTerms}
        </p>
      </section>
    </main>
  );
}
