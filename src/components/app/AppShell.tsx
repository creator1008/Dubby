"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { api } from "@/lib/api";
import { LanguageSwitcher } from "@/components/landing/LanguageSwitcher";
import { useAppDictionary } from "@/lib/i18n/locale-context";
import { isAdminSession, useAuthSession } from "@/components/app/AuthBoundary";
import { getSupabase } from "@/lib/supabase";
import { withBasePath } from "@/lib/base-path";
import { PwaInstallPrompt, shouldOfferPwaInstall } from "@/components/pwa/PwaInstallPrompt";

type MenuPlacement = {
  top?: number;
  bottom?: number;
  left: number;
};

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const text = useAppDictionary();
  const session = useAuthSession();
  const [balance, setBalance] = useState<number | null>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPlacement, setMenuPlacement] = useState<MenuPlacement | null>(null);
  const [installRequestId, setInstallRequestId] = useState(0);
  const [installAvailable, setInstallAvailable] = useState(false);
  const [portalReady, setPortalReady] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);
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

  const updateMenuPlacement = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const mobile = window.matchMedia("(max-width: 767px)").matches;
    const menuWidth = Math.min(16 * 16, window.innerWidth - 16);
    // Left-align the menu with the trigger; clamp into the viewport.
    const left = Math.min(
      Math.max(8, rect.left),
      window.innerWidth - menuWidth - 8,
    );
    if (mobile) {
      setMenuPlacement({
        bottom: Math.max(8, window.innerHeight - rect.top + 10),
        left,
      });
      return;
    }
    setMenuPlacement({
      top: rect.bottom + 8,
      left,
    });
  }, []);

  useLayoutEffect(() => {
    if (!menuOpen) {
      setMenuPlacement(null);
      return;
    }
    updateMenuPlacement();
    window.addEventListener("resize", updateMenuPlacement);
    window.addEventListener("scroll", updateMenuPlacement, true);
    return () => {
      window.removeEventListener("resize", updateMenuPlacement);
      window.removeEventListener("scroll", updateMenuPlacement, true);
    };
  }, [menuOpen, updateMenuPlacement]);

  useEffect(() => {
    if (!menuOpen) return;
    // Longer defer on touch devices so the opening tap cannot instantly dismiss.
    let remove: (() => void) | undefined;
    const timer = window.setTimeout(() => {
      const onPointerDown = (event: PointerEvent) => {
        const target = event.target as Node;
        if (
          menuRef.current?.contains(target) ||
          popoverRef.current?.contains(target)
        ) {
          return;
        }
        setMenuOpen(false);
      };
      const onKeyDown = (event: KeyboardEvent) => {
        if (event.key === "Escape") setMenuOpen(false);
      };
      document.addEventListener("pointerdown", onPointerDown);
      document.addEventListener("keydown", onKeyDown);
      remove = () => {
        document.removeEventListener("pointerdown", onPointerDown);
        document.removeEventListener("keydown", onKeyDown);
      };
    }, 320);
    return () => {
      window.clearTimeout(timer);
      remove?.();
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

  const menuPopover =
    menuOpen && portalReady
      ? createPortal(
          <div
            ref={popoverRef}
            className="account-menu-popover account-menu-popover-portal"
            id={menuId}
            role="menu"
            style={{
              position: "fixed",
              top: menuPlacement?.top,
              bottom: menuPlacement?.bottom ?? (menuPlacement?.top == null ? 72 : undefined),
              left: menuPlacement?.left ?? 8,
              right: "auto",
            }}
          >
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
          </div>,
          document.body,
        )
      : null;

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
          {session && (
            <div className="account-menu" ref={menuRef}>
              <button
                ref={triggerRef}
                type="button"
                className="account-menu-trigger"
                aria-expanded={menuOpen}
                aria-controls={menuId}
                aria-haspopup="menu"
                onClick={(event) => {
                  event.stopPropagation();
                  if (menuOpen) {
                    setMenuOpen(false);
                    return;
                  }
                  updateMenuPlacement();
                  setMenuOpen(true);
                }}
              >
                <span className="nav-label-full">{displayName}</span>
                <span className="nav-label-short" aria-hidden="true">
                  {displayName.trim().charAt(0).toUpperCase() || "·"}
                </span>
              </button>
              {menuPopover}
            </div>
          )}
        </nav>
      </header>
      <div className="app-main">{children}</div>
      <PwaInstallPrompt
        openRequestId={installRequestId}
        onAvailabilityChange={setInstallAvailable}
      />
    </div>
  );
}
