"use client";

import { useEffect, useState } from "react";
import type { Session, SupabaseClient } from "@supabase/supabase-js";
import { getSupabase } from "@/lib/supabase";
import { useAppDictionary } from "@/lib/i18n/locale-context";

type CallbackSessionResult = {
  data: { session: Session | null };
  error: Error | null;
};

let callbackSessionPromise: Promise<CallbackSessionResult> | null = null;

function completeCallbackSession(supabase: SupabaseClient, code: string | null) {
  if (!callbackSessionPromise) {
    callbackSessionPromise = (async () => {
      const result = code
        ? await supabase.auth.exchangeCodeForSession(code)
        : await supabase.auth.getSession();
      return {
        data: { session: result.data.session },
        error: result.error,
      };
    })();
  }
  return callbackSessionPromise;
}

export default function AuthCallbackPage() {
  const text = useAppDictionary();
  const [message, setMessage] = useState(
    getSupabase() ? text.checkingLogin : text.supabaseRequired,
  );

  useEffect(() => {
    const supabase = getSupabase();
    if (!supabase) return;
    let active = true;

    const finish = async () => {
      const params = new URLSearchParams(window.location.search);
      const callbackError =
        params.get("error_description") ?? params.get("error");
      if (callbackError) {
        if (active) setMessage(callbackError);
        return;
      }
      const code = params.get("code");
      const { data, error } = await completeCallbackSession(supabase, code);
      if (!active) return;
      if (code && !error) {
        const cleanUrl = new URL(window.location.href);
        cleanUrl.searchParams.delete("code");
        window.history.replaceState({}, document.title, cleanUrl.toString());
      }
      if (error || !data.session) {
        setMessage(error?.message ?? "로그인 세션을 만들지 못했습니다.");
        return;
      }
      const user = data.session.user;
      const metadata = user.user_metadata ?? {};
      const country = window.sessionStorage.getItem("dubby-signup-country");
      const displayName =
        metadata.full_name ?? metadata.name ?? metadata.user_name ?? user.email ?? "";
      const { data: existingProfile } = await supabase
        .from("profiles")
        .select("country")
        .eq("id", user.id)
        .maybeSingle();
      if (!active) return;
      const profile = {
        email: user.email ?? null,
        display_name: String(displayName),
        auth_provider: String(user.app_metadata?.provider ?? ""),
        ...(country && !existingProfile?.country ? { country } : {}),
        last_login_at: new Date().toISOString(),
      };
      const { error: profileError } = await supabase
        .from("profiles")
        .update(profile)
        .eq("id", user.id);
      if (!active) return;
      if (profileError) {
        setMessage(`프로필 저장 실패: ${profileError.message}`);
        return;
      }
      window.sessionStorage.removeItem("dubby-signup-country");
      window.location.replace("/app");
    };

    void finish();
    return () => {
      active = false;
    };
  }, []);

  return (
    <main className="auth-page">
      <section className="auth-card">
        <h1>Dubby</h1>
        <p className="muted">{message}</p>
      </section>
    </main>
  );
}
