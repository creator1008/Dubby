"use client";

import { useEffect } from "react";
import { withBasePath } from "@/lib/base-path";

/**
 * Next metadata sometimes emits root-absolute `/manifest.json` without basePath
 * on static GitHub Pages builds. Force the correct links at runtime.
 */
export function PwaHeadFix() {
  useEffect(() => {
    const ensureLink = (rel: string, href: string, attrs: Record<string, string> = {}) => {
      const absolute = new URL(href, window.location.origin).pathname;
      const existing = Array.from(
        document.querySelectorAll<HTMLLinkElement>(`link[rel="${rel}"]`),
      );
      for (const node of existing) {
        const path = new URL(node.href, window.location.origin).pathname;
        if (path === absolute || path.endsWith(absolute)) {
          for (const [key, value] of Object.entries(attrs)) {
            node.setAttribute(key, value);
          }
          return;
        }
      }
      // Remove incorrect root links that miss /Dubby
      for (const node of existing) {
        const path = new URL(node.href, window.location.origin).pathname;
        if (
          path === "/manifest.json" ||
          path === "/favicon.ico" ||
          path.startsWith("/icons/")
        ) {
          node.remove();
        }
      }
      const link = document.createElement("link");
      link.rel = rel;
      link.href = href;
      for (const [key, value] of Object.entries(attrs)) {
        link.setAttribute(key, value);
      }
      document.head.appendChild(link);
    };

    ensureLink("manifest", withBasePath("/manifest.json"));
    ensureLink("icon", withBasePath("/icons/icon-192.png"), {
      sizes: "192x192",
      type: "image/png",
    });
    ensureLink("icon", withBasePath("/icons/icon-512.png"), {
      sizes: "512x512",
      type: "image/png",
    });
    ensureLink("apple-touch-icon", withBasePath("/icons/apple-touch-icon.png"), {
      sizes: "180x180",
    });
  }, []);

  return null;
}
