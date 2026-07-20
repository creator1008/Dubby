"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { useAuthSession } from "@/components/app/AuthBoundary";
import { getSupabase } from "@/lib/supabase";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const text = useAppDictionary();
  const session = useAuthSession();
  const [balance, setBalance] = useState<number | null>(null);
  const refreshBalance = useCallback(() => {
    void api.credits().then((data) => setBalance(data.balance_minutes)).catch(() => setBalance(null));
  }, []);
  useEffect(() => {
    refreshBalance();
    window.addEventListener("credits-changed", refreshBalance);
    return () => window.removeEventListener("credits-changed", refreshBalance);
  }, [refreshBalance]);

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <Link href="/" className="brand-mark">
          Dubby
        </Link>
        <nav className="app-nav">
          <LanguageSwitcher />
          <Link href="/app/new" className="btn-primary header-new-dub">
            {text.newDub}
          </Link>
          <Link
            href="/app"
            className={`btn-ghost header-history${pathname === "/app" ? " active" : ""}`}
          >
            {text.history}
          </Link>
          {session && (
            <details className="account-menu">
              <summary className="account-menu-trigger">
                {session.user.user_metadata?.full_name ?? session.user.email}
              </summary>
              <div className="account-menu-popover">
                <Link href="/app/billing" className="account-menu-item">
                  <span>{text.credits}</span>
                  <strong>
                    {balance === null ? "—" : `${balance.toFixed(1)} ${text.minutes}`}
                  </strong>
                </Link>
                <button
                  type="button"
                  className="account-menu-item"
                  onClick={() => void getSupabase()?.auth.signOut()}
                >
                  {text.logout}
                </button>
              </div>
            </details>
          )}
        </nav>
      </header>
      <div className="app-main">{children}</div>
    </div>
  );
}
