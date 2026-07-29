/** Repo subpath when hosted on GitHub Pages (e.g. `/Dubby`). Empty for local/root hosts. */
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

/** Canonical public site origin+base (no trailing slash), baked for Pages builds. */
export const SITE_URL = (process.env.NEXT_PUBLIC_SITE_URL ?? "").replace(/\/$/, "");

/** Prefix a root-relative path for `window.location` and raw asset URLs. */
export function withBasePath(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  return `${BASE_PATH}${path}`;
}

/**
 * Absolute URL for Supabase OAuth / email redirects.
 * Prefer the baked SITE_URL so GitHub Pages never falls back to localhost Site URL mismatches.
 */
export function getAuthRedirectUrl(path = "/auth/callback/"): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  if (SITE_URL) return `${SITE_URL}${normalized}`;
  if (typeof window !== "undefined") {
    return `${window.location.origin}${withBasePath(normalized)}`;
  }
  return withBasePath(normalized);
}
