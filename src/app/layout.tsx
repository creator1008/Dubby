import type { Metadata, Viewport } from "next";
import Script from "next/script";
import { Figtree, Syne } from "next/font/google";
import { LocaleProvider } from "@/lib/i18n/locale-context";
import { PwaRegistrar } from "@/components/pwa/PwaRoot";
import { PwaHeadFix } from "@/components/pwa/PwaHeadFix";
import { BASE_PATH, withBasePath } from "@/lib/base-path";
import "./globals.css";

const syne = Syne({
  variable: "--font-syne",
  subsets: ["latin"],
  weight: ["600", "700", "800"],
});

const figtree = Figtree({
  variable: "--font-figtree",
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
});

export const metadata: Metadata = {
  title: "Dubby — AI 영상 다국어 더빙",
  description:
    "유튜브·인강·홍보 영상을 영어·한국어·베트남어로 현지화하는 AI 더빙 서비스",
  applicationName: "Dubby",
  // Next static export + GitHub Pages does not always prefix metadata URLs —
  // bake basePath in explicitly so Android can resolve the web manifest.
  manifest: withBasePath("/manifest.json"),
  appleWebApp: {
    capable: true,
    statusBarStyle: "default",
    title: "Dubby",
  },
  icons: {
    icon: [
      { url: withBasePath("/favicon.ico"), sizes: "any" },
      {
        url: withBasePath("/icons/icon-192.png"),
        sizes: "192x192",
        type: "image/png",
      },
      {
        url: withBasePath("/icons/icon-512.png"),
        sizes: "512x512",
        type: "image/png",
      },
    ],
    apple: [
      {
        url: withBasePath("/icons/apple-touch-icon.png"),
        sizes: "180x180",
      },
    ],
  },
  other: BASE_PATH
    ? {
        "mobile-web-app-capable": "yes",
      }
    : undefined,
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: "#0F9C8A",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={`${syne.variable} ${figtree.variable} h-full`}>
      <body className="min-h-full antialiased">
        <Script id="dubby-pwa-capture" strategy="beforeInteractive">
          {`(function(){try{window.__dubbyDeferredPrompt=window.__dubbyDeferredPrompt||null;window.addEventListener("beforeinstallprompt",function(e){e.preventDefault();window.__dubbyDeferredPrompt=e;window.dispatchEvent(new Event("dubby-pwa-prompt-ready"));});window.addEventListener("appinstalled",function(){window.__dubbyDeferredPrompt=null;window.dispatchEvent(new Event("dubby-pwa-installed"));});}catch(_){}})();`}
        </Script>
        <LocaleProvider>
          <PwaHeadFix />
          <PwaRegistrar />
          {children}
        </LocaleProvider>
      </body>
    </html>
  );
}
