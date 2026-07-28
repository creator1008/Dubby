/** Repo subpath when hosted on GitHub Pages (e.g. `/Dubby`). Empty for local/root hosts. */
export const BASE_PATH = (process.env.NEXT_PUBLIC_BASE_PATH ?? "").replace(/\/$/, "");

/** Prefix a root-relative path for `window.location` and raw asset URLs. */
export function withBasePath(path: string): string {
  if (!path.startsWith("/") || path.startsWith("//")) return path;
  return `${BASE_PATH}${path}`;
}
