"use client";

import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";
import { createPortal } from "react-dom";
import type { Provider } from "@supabase/supabase-js";
import { getAuthRedirectUrl, withBasePath } from "@/lib/base-path";
import { getSupabase } from "@/lib/supabase";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";

type OAuthProvider = Extract<Provider, "google" | "facebook" | "kakao">;

const PROVIDERS: Array<{
  id: OAuthProvider;
  className: string;
  labelKey: "continueGoogle" | "continueFacebook" | "continueKakao";
  disabled?: boolean;
  disabledLabelKey?: "continueFacebookDisabled" | "continueKakaoDisabled";
}> = [
  { id: "google", className: "oauth-google", labelKey: "continueGoogle" },
  {
    id: "facebook",
    className: "oauth-facebook",
    labelKey: "continueFacebook",
    disabled: true,
    disabledLabelKey: "continueFacebookDisabled",
  },
  {
    id: "kakao",
    className: "oauth-kakao",
    labelKey: "continueKakao",
    disabled: true,
    disabledLabelKey: "continueKakaoDisabled",
  },
];

const COUNTRIES = [
  ["KR", "대한민국"],
  ["US", "United States"],
  ["VN", "Việt Nam"],
  ["JP", "日本"],
  ["CN", "中国"],
  ["OTHER", "기타 / Other"],
];

const MIN_PASSWORD_LENGTH = 8;

export default function LoginPage() {
  const supabase = getSupabase();
  const text = useAppDictionary();
  const signupTitleId = useId();
  const [country, setCountry] = useState("KR");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [signupOpen, setSignupOpen] = useState(false);
  const [signupName, setSignupName] = useState("");
  const [signupEmail, setSignupEmail] = useState("");
  const [signupPassword, setSignupPassword] = useState("");
  const [signupPasswordConfirm, setSignupPasswordConfirm] = useState("");
  const [busy, setBusy] = useState<OAuthProvider | "signin" | "signup" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [signupError, setSignupError] = useState<string | null>(null);
  const [signupDoneMessage, setSignupDoneMessage] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [mounted, setMounted] = useState(false);

  const mapAuthError = (message: string, code?: string) => {
    const normalized = `${code ?? ""} ${message}`.toLowerCase();
    if (normalized.includes("rate_limit") || normalized.includes("rate limit")) {
      return text.emailRateLimited;
    }
    if (normalized.includes("email_address_invalid") || normalized.includes("is invalid")) {
      return text.emailInvalid;
    }
    if (
      normalized.includes("already") ||
      normalized.includes("registered") ||
      normalized.includes("exists")
    ) {
      return text.emailAlreadyRegistered;
    }
    return message;
  };

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    if (!signupOpen) return;
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, [signupOpen]);

  useEffect(() => {
    if (!supabase) return;
    void supabase.auth.getSession().then(({ data }) => {
      if (data.session) window.location.replace(withBasePath("/app/"));
    });
  }, [supabase]);

  useEffect(() => {
    if (!signupOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && busy !== "signup") {
        setSignupOpen(false);
        setSignupError(null);
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [signupOpen, busy]);

  const closeSignup = () => {
    if (busy === "signup") return;
    setSignupOpen(false);
    setSignupError(null);
    setSignupDoneMessage(null);
  };

  const openSignup = () => {
    setSignupOpen(true);
    setSignupError(null);
    setSignupDoneMessage(null);
    setError(null);
    setInfo(null);
    setSignupName("");
    setSignupEmail(email.trim());
    setSignupPassword("");
    setSignupPasswordConfirm("");
  };

  const signIn = async (provider: OAuthProvider) => {
    if (!supabase) {
      setError(text.supabaseRequired);
      return;
    }
    setBusy(provider);
    setError(null);
    setInfo(null);
    window.sessionStorage.setItem("dubby-signup-country", country);
    const { error: oauthError } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: getAuthRedirectUrl("/auth/callback/"),
      },
    });
    if (oauthError) {
      setError(oauthError.message);
      setBusy(null);
    }
  };

  const submitSignIn = async (event: FormEvent) => {
    event.preventDefault();
    if (!supabase) {
      setError(text.supabaseRequired);
      return;
    }
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !password) {
      setError(text.loginCredentialsInvalid);
      return;
    }
    setBusy("signin");
    setError(null);
    setInfo(null);
    window.sessionStorage.setItem("dubby-signup-country", country);

    const { error: signInError } = await supabase.auth.signInWithPassword({
      email: trimmedEmail,
      password,
    });
    if (signInError) {
      setError(text.loginCredentialsInvalid);
      setBusy(null);
      return;
    }
    window.location.replace(withBasePath("/app/"));
  };

  const submitSignUp = async (event: FormEvent) => {
    event.preventDefault();
    if (!supabase) {
      setSignupError(text.supabaseRequired);
      return;
    }
    const trimmedEmail = signupEmail.trim();
    const trimmedName = signupName.trim();
    if (!trimmedName) {
      setSignupError(text.nameRequired);
      return;
    }
    if (!trimmedEmail) {
      setSignupError(text.emailAuthInvalid);
      return;
    }
    if (signupPassword.length < MIN_PASSWORD_LENGTH) {
      setSignupError(text.passwordTooShort);
      return;
    }
    if (signupPassword !== signupPasswordConfirm) {
      setSignupError(text.passwordMismatch);
      return;
    }

    setBusy("signup");
    setSignupError(null);
    setSignupDoneMessage(null);
    window.sessionStorage.setItem("dubby-signup-country", country);

    const { data, error: signUpError } = await supabase.auth.signUp({
      email: trimmedEmail,
      password: signupPassword,
      options: {
        data: {
          country,
          full_name: trimmedName,
          name: trimmedName,
        },
        emailRedirectTo: getAuthRedirectUrl("/auth/callback/"),
      },
    });
    if (signUpError) {
      setSignupError(mapAuthError(signUpError.message, signUpError.code));
      setBusy(null);
      return;
    }

    // Supabase returns a user with empty identities when the email is already registered
    // and email enumeration protection is enabled.
    if ((data.user?.identities?.length ?? 0) === 0) {
      setSignupError(text.emailAlreadyRegistered);
      setBusy(null);
      return;
    }

    if (data.session?.user?.id) {
      await supabase
        .from("profiles")
        .update({ display_name: trimmedName })
        .eq("id", data.session.user.id);
    }

    setBusy(null);
    setSignupName("");
    setSignupPassword("");
    setSignupPasswordConfirm("");
    setEmail(trimmedEmail);
    setPassword("");

    if (data.session) {
      setSignupDoneMessage(text.signUpComplete);
      setInfo(text.signUpComplete);
      window.setTimeout(() => {
        window.location.replace(withBasePath("/app/"));
      }, 400);
      return;
    }

    // Email confirmation enabled: account was created, but login needs confirm (or Confirm email off).
    setSignupDoneMessage(text.emailConfirmSent);
    setInfo(text.emailConfirmSent);
  };

  return (
    <main className="auth-page">
      <header className="auth-header">
        <Link href="/" className="brand-mark">Dubby</Link>
        <div className="auth-header-actions">
          <button type="button" className="auth-signup-link" onClick={openSignup}>
            {text.signUp}
          </button>
          <LanguageSwitcher />
        </div>
      </header>

      <section className="auth-card">
        <div>
          <p className="eyebrow">ACCOUNT</p>
          <h1>{text.signIn}</h1>
          <p className="muted">{text.loginDescription}</p>
        </div>
        <label className="auth-field">
          {text.country}
          <select value={country} onChange={(event) => setCountry(event.target.value)}>
            {COUNTRIES.map(([code, name]) => (
              <option value={code} key={code}>{name}</option>
            ))}
          </select>
        </label>

        <form className="auth-email-form" onSubmit={submitSignIn}>
          <label className="auth-field auth-field-lg">
            {text.emailOrId}
            <input
              type="email"
              autoComplete="email"
              value={email}
              onChange={(event) => {
                setEmail(event.target.value);
                if (error) setError(null);
              }}
              placeholder="you@example.com"
              disabled={Boolean(busy)}
              required
            />
          </label>
          <label className="auth-field auth-field-lg">
            {text.password}
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => {
                setPassword(event.target.value);
                if (error) setError(null);
              }}
              disabled={Boolean(busy)}
              required
            />
          </label>
          {error && <p className="form-msg err">{error}</p>}
          {info && <p className="form-msg ok">{info}</p>}
          <button type="submit" className="auth-email-submit" disabled={Boolean(busy)}>
            {busy === "signin" ? text.connecting : text.signIn}
          </button>
        </form>

        <div className="auth-divider" aria-hidden="true">
          <span>{text.orContinueWith}</span>
        </div>

        <div className="oauth-list">
          {PROVIDERS.map((provider) => (
            <button
              key={provider.id}
              type="button"
              className={`oauth-button ${provider.className}${
                provider.disabled ? " oauth-disabled" : ""
              }`}
              disabled={Boolean(busy) || provider.disabled}
              title={
                provider.disabled && provider.disabledLabelKey
                  ? text[provider.disabledLabelKey]
                  : undefined
              }
              onClick={() => {
                if (!provider.disabled) void signIn(provider.id);
              }}
            >
              {busy === provider.id
                ? text.connecting
                : provider.disabled && provider.disabledLabelKey
                  ? text[provider.disabledLabelKey]
                  : text[provider.labelKey]}
            </button>
          ))}
        </div>
        <p className="auth-terms muted">{text.loginTerms}</p>
      </section>

      {mounted &&
        signupOpen &&
        createPortal(
          <div
            className="auth-modal-backdrop"
            role="presentation"
            onClick={(event) => {
              if (event.target === event.currentTarget) closeSignup();
            }}
          >
            <div
              className="auth-modal"
              role="dialog"
              aria-modal="true"
              aria-labelledby={signupTitleId}
            >
              <div className="auth-modal-head">
                <h2 id={signupTitleId}>{text.signUp}</h2>
                <button
                  type="button"
                  className="auth-modal-close"
                  aria-label={text.close}
                  onClick={closeSignup}
                  disabled={busy === "signup"}
                >
                  ×
                </button>
              </div>
              {signupDoneMessage ? (
                <div className="auth-signup-done">
                  <p className="form-msg ok">{signupDoneMessage}</p>
                  <button type="button" className="auth-email-submit" onClick={closeSignup}>
                    {text.close}
                  </button>
                </div>
              ) : (
                <form className="auth-email-form" onSubmit={submitSignUp}>
                  <label className="auth-field">
                    {text.displayName}
                    <input
                      type="text"
                      autoComplete="name"
                      value={signupName}
                      onChange={(event) => {
                        setSignupName(event.target.value);
                        if (signupError) setSignupError(null);
                      }}
                      placeholder={text.displayNamePlaceholder}
                      disabled={busy === "signup"}
                      required
                      autoFocus
                      maxLength={80}
                    />
                  </label>
                  <label className="auth-field">
                    Email
                    <input
                      type="email"
                      autoComplete="email"
                      value={signupEmail}
                      onChange={(event) => {
                        setSignupEmail(event.target.value);
                        if (signupError) setSignupError(null);
                      }}
                      placeholder="you@example.com"
                      disabled={busy === "signup"}
                      required
                    />
                  </label>
                  <label className="auth-field">
                    {text.password}
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={signupPassword}
                      onChange={(event) => {
                        setSignupPassword(event.target.value);
                        if (signupError) setSignupError(null);
                      }}
                      minLength={MIN_PASSWORD_LENGTH}
                      disabled={busy === "signup"}
                      required
                    />
                  </label>
                  <label className="auth-field">
                    {text.passwordConfirm}
                    <input
                      type="password"
                      autoComplete="new-password"
                      value={signupPasswordConfirm}
                      onChange={(event) => {
                        setSignupPasswordConfirm(event.target.value);
                        if (signupError) setSignupError(null);
                      }}
                      minLength={MIN_PASSWORD_LENGTH}
                      disabled={busy === "signup"}
                      required
                    />
                  </label>
                  {signupError && <p className="form-msg err">{signupError}</p>}
                  <button
                    type="submit"
                    className="auth-email-submit"
                    disabled={busy === "signup"}
                  >
                    {busy === "signup" ? text.connecting : text.signUp}
                  </button>
                </form>
              )}
            </div>
          </div>,
          document.body,
        )}
    </main>
  );
}
