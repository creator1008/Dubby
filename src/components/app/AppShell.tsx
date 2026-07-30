"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { isAdminSession, useAuthSession } from "@/components/app/AuthBoundary";
import { getSupabase } from "@/lib/supabase";
import { withBasePath } from "@/lib/base-path";
import { PwaInstallPrompt, shouldOfferPwaInstall } from "@/components/pwa/PwaInstallPrompt";

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const text = useAppDictionary();
  const session = useAuthSession();
  const [balance, setBalance] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [installRequestId, setInstallRequestId] = useState(0);
  const [installAvailable, setInstallAvailable] = useState(false);
  const [portalReady, setPortalReady] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const refreshBalance = useCallback(() => {
    if (!session) {
      setBalance(null);
      return;
    }
    void api.credits().then((data) => setBalance(data.balance_minutes)).catch(() => setBalance(null));
  }, [session]);

  useEffect(() => {
    setPortalReady(true);
  }, []);

  useEffect(() => {
    setInstallAvailable(shouldOfferPwaInstall());
  }, []);

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
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
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

  const onAdmin = pathname === "/admin" || pathname.startsWith("/admin/");

  const menuPortal =
    menuOpen && portalReady
      ? createPortal(
          <div
            className="account-menu-overlay"
            role="presentation"
            onClick={() => setMenuOpen(false)}
          >
            <div
              ref={panelRef}
              className="account-menu-sheet"
              id={menuId}
              role="menu"
              aria-label={displayName}
              onClick={(event) => event.stopPropagation()}
            >
              <div className="account-menu-sheet-head">
                <strong>{displayName}</strong>
                <button
                  type="button"
                  className="auth-modal-close"
                  aria-label={text.close}
                  onClick={() => setMenuOpen(false)}
                >
                  ×
                </button>
              </div>
              <Link
                href="/app/billing"
                className="account-menu-item"
                role="menuitem"
                onClick={() => setMenuOpen(false)}
              >
                {text.topUpCredits}
              </Link>
              {onAdmin ? (
                <Link
                  href="/app"
                  className="account-menu-item"
                  role="menuitem"
                  onClick={() => setMenuOpen(false)}
                >
                  {text.exitAdmin}
                </Link>
              ) : (
                isAdminSession(session) && (
                  <Link
                    href="/admin"
                    className="account-menu-item"
                    role="menuitem"
                    onClick={() => setMenuOpen(false)}
                  >
                    {text.adminTitle}
                  </Link>
                )
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
          </div>,
          document.body,
        )
      : null;

  return (
    <div className="app-shell">
      {/*
        Keep nav OUTSIDE the blurred topbar. backdrop-filter creates a containing
        block so position:fixed children stick to the header on mobile WebKit.
      */}
      <div className="app-chrome">
        <header className="app-topbar">
          <Link href="/" className="brand-mark">
            Dubby
          </Link>
          {session && (
            <button
              ref={triggerRef}
              type="button"
              className="account-menu-trigger account-menu-trigger-header"
              aria-expanded={menuOpen}
              aria-controls={menuId}
              aria-haspopup="menu"
              onClick={() => setMenuOpen((open) => !open)}
            >
              <span className="account-menu-trigger-name">{displayName}</span>
            </button>
          )}
        </header>
        <nav className="app-nav" aria-label="Main">
          {session && (
            <span className="credits-pill" aria-label={text.credits}>
              {balance === null ? "—" : `${balance.toFixed(1)}${text.minutes}`}
            </span>
          )}
          <LanguageSwitcher />
          {installAvailable && (
            <button
              type="button"
              className="btn-ghost header-install-link"
              onClick={() => setInstallRequestId((id) => id + 1)}
            >
              <span className="nav-label-full">{text.addToHome}</span>
              <span className="nav-label-short" aria-hidden="true">
                ⌂
              </span>
            </button>
          )}
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
        </nav>
      </div>
      <div className="app-main">{children}</div>
      {menuPortal}
      <PwaInstallPrompt
        openRequestId={installRequestId}
        onAvailabilityChange={setInstallAvailable}
      />
    </div>
  );
}
