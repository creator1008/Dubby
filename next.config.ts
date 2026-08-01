import type { NextConfig } from "next";

const isGitHubPages = process.env.GITHUB_PAGES === "true";
/** Custom domain (dubbyai.com) is served at site root — no /Dubby prefix. */
const useCustomDomain =
  process.env.DUBBY_CUSTOM_DOMAIN === "true" ||
  (process.env.NEXT_PUBLIC_SITE_URL || "").includes("dubbyai.com");
const basePath = (
  process.env.NEXT_PUBLIC_BASE_PATH ??
  (isGitHubPages && !useCustomDomain ? "/Dubby" : "")
).replace(/\/$/, "");
/** Public app URL for OAuth/email redirects — never fall back to localhost. */
const DEFAULT_SITE_URL = "https://dubbyai.com";
const siteUrl =
  process.env.NEXT_PUBLIC_SITE_URL?.replace(/\/$/, "") || DEFAULT_SITE_URL;

const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  ...(basePath
    ? {
        basePath,
        assetPrefix: basePath,
      }
    : {}),
  env: {
    NEXT_PUBLIC_BASE_PATH: basePath,
    NEXT_PUBLIC_SITE_URL: siteUrl,
  },
};

export default nextConfig;
