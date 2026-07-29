"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { api } from "@/lib/api";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { isAdminSession, useAuthSession } from "@/components/app/AuthBoundary";
import { getSupabase } from "@/lib/supabase";
import { withBasePath } from "@/lib/base-path";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const text = useAppDictionary();
  const session = useAuthSession();
  const [balance, setBalance] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const refreshBalance = useCallback(() => {
    if (!session) {
      setBalance(null);
      return;
    }
    void api.credits().then((data) => setBalance(data.balance_minutes)).catch(() => setBalance(null));
  }, [session]);

  useEffect(() => {
    refreshBalance();
    window.addEventListener("credits-changed", refreshBalance);
    return () => window.removeEventListener("credits-changed", refreshBalance);
  }, [refreshBalance]);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menuOpen]);

  const displayName =
    session?.user.user_metadata?.full_name ??
    session?.user.user_metadata?.name ??
    session?.user.email ??
    text.credits;

  const handleLogout = async () => {
    setMenuOpen(false);
    try {
      await getSupabase()?.auth.signOut();
    } finally {
      window.location.assign(withBasePath("/"));
    }
  };

  return (
    <div className="app-shell">
      <header className="app-topbar">
        <Link href="/" className="brand-mark">
          Dubby
        </Link>
        <nav className="app-nav">
          {session && (
            <span className="credits-pill" aria-label={text.credits}>
              {balance === null ? "—" : `${balance.toFixed(1)}${text.minutes}`}
            </span>
          )}
          <LanguageSwitcher />
          <Link href="/app/new" className="btn-primary header-new-dub">
            <span className="nav-label-full">{text.newDub}</span>
            <span className="nav-label-short" aria-hidden="true">
              +
            </span>
          </Link>
          <Link
            href="/app"
            className={`btn-ghost header-history${pathname === "/app" || pathname === "/app/" ? " active" : ""}`}
          >
            <span className="nav-label-full">{text.history}</span>
            <span className="nav-label-short" aria-hidden="true">
              ≡
            </span>
          </Link>
          {session && (
            <div className="account-menu" ref={menuRef}>
              <button
                type="button"
                className="account-menu-trigger"
                aria-expanded={menuOpen}
                aria-controls={menuId}
                aria-haspopup="menu"
                onClick={() => setMenuOpen((open) => !open)}
              >
                {displayName}
              </button>
              {menuOpen && (
                <div className="account-menu-popover" id={menuId} role="menu">
                  <Link
                    href="/app/billing"
                    className="account-menu-item"
                    role="menuitem"
                    onClick={() => setMenuOpen(false)}
                  >
                    {text.topUpCredits}
                  </Link>
                  {isAdminSession(session) && (
                    <Link
                      href="/admin"
                      className="account-menu-item"
                      role="menuitem"
                      onClick={() => setMenuOpen(false)}
                    >
                      {text.adminTitle}
                    </Link>
                  )}
                  <button
                    type="button"
                    className="account-menu-item"
                    role="menuitem"
                    onClick={() => void handleLogout()}
                  >
                    {text.logout}
                  </button>
                </div>
              )}
            </div>
          )}
        </nav>
      </header>
      <div className="app-main">{children}</div>
    </div>
  );
}
