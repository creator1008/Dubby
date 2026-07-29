"use client";

import Link from "next/link";
import { useLocale } from "@/lib/i18n/locale-context";
import { LanguageSwitcher } from "./LanguageSwitcher";

type SiteHeaderProps = {
  showInstallLink?: boolean;
  onInstallClick?: () => void;
};

export function SiteHeader({
  showInstallLink = false,
  onInstallClick,
}: SiteHeaderProps) {
  const { dict } = useLocale();

  return (
    <header className="site-header">
      <Link href="/" className="brand-mark">
        {dict.brand}
      </Link>
      <nav className="header-actions">
        <LanguageSwitcher />
        {showInstallLink && onInstallClick ? (
          <button
            type="button"
            className="header-app-link header-install-link"
            onClick={onInstallClick}
          >
            홈 화면에 추가
          </button>
        ) : null}
        <Link href="/app/new" className="header-app-link header-app-link-primary">
          {dict.newDub}
        </Link>
        <Link href="/app" className="header-app-link">
          {dict.dubbingHistory}
        </Link>
        <Link href="/login" className="header-app-link">
          {dict.login}
        </Link>
      </nav>
    </header>
  );
}
