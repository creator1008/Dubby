"use client";

import type { Session } from "@supabase/supabase-js";
import Link from "next/link";
import { createContext, useContext, useEffect, useState } from "react";
import { getSupabase } from "@/lib/supabase";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { isDemoMode } from "@/lib/demo-api";

const AuthContext = createContext<Session | null>(null);

export function useAuthSession() {
  return useContext(AuthContext);
}

export function AuthBoundary({ children }: { children: React.ReactNode }) {
  const supabase = getSupabase();
  const text = useAppDictionary();
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(Boolean(supabase));

  useEffect(() => {
    if (!supabase) return;
    let active = true;
    void supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      setSession(data.session);
      setLoading(false);
    });
    const { data } = supabase.auth.onAuthStateChange((_event, nextSession) => {
      if (!active) return;
      setSession(nextSession);
      setLoading(false);
    });
    return () => {
      active = false;
      data.subscription.unsubscribe();
    };
  }, [supabase]);

  if (!supabase) {
    if (isDemoMode) {
      return <AuthContext.Provider value={null}>{children}</AuthContext.Provider>;
    }
    return <p className="form-msg err auth-loading">{text.authConfigMissing}</p>;
  }
  if (loading) {
    return <p className="muted auth-loading">{text.checkingLogin}</p>;
  }
  if (!session) {
    return (
      <div className="auth-required">
        <h1>{text.loginRequired}</h1>
        <Link className="btn-primary" href="/login">
          {text.loginTitle}
        </Link>
      </div>
    );
  }
  return <AuthContext.Provider value={session}>{children}</AuthContext.Provider>;
}

export function isAdminSession(session: Session | null) {
  return session?.user.app_metadata?.role === "admin";
}
