/** Repo subpath when hosted on GitHub Pages (e.g. `/Dubby`). Empty for local/root hosts. */
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

/** Canonical public site origin+base (no trailing slash). Never localhost. */
export const DEFAULT_SITE_URL = "https://creator1008.github.io/Dubby";
export const SITE_URL = (
  process.env.NEXT_PUBLIC_SITE_URL || DEFAULT_SITE_URL
).replace(/\/$/, "");

/** Prefix a root-relative path for `window.location` and raw asset URLs. */
export function withBasePath(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  return `${BASE_PATH}${path}`;
}

/**
 * Absolute URL for Supabase OAuth / email redirects.
 * Always uses the public GitHub Pages URL so Google login never returns to localhost.
 */
export function getAuthRedirectUrl(path = "/auth/callback/"): string {
  const normalized = path.startsWith("/") ? path : `/${path}`;
  return `${SITE_URL}${normalized}`;
}
