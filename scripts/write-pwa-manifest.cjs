/**
 * Write public/manifest.json for PWA install.
 * On GitHub Pages the home-screen icon must open
 * https://creator1008.github.io/Dubby/
 */
const fs = require("fs");
const path = require("path");

const isPages = process.env.GITHUB_PAGES === "true";
const siteOrigin = isPages ? "https://creator1008.github.io/Dubby" : "";
const abs = (p) => {
  const normalized = p.startsWith("/") ? p : `/${p}`;
  return siteOrigin ? `${siteOrigin}${normalized === "/" ? "/" : normalized}` : normalized;
};

const manifest = {
  name: "Dubby",
  short_name: "Dubby",
  description: "AI 영상 다국어 더빙",
  start_url: abs("/"),
  scope: abs("/"),
  id: abs("/"),
  display: "standalone",
  orientation: "any",
  background_color: "#0F9C8A",
  theme_color: "#0F9C8A",
  lang: "ko",
  prefer_related_applications: false,
  icons: [
    {
      src: abs("/icons/icon-192.png"),
      sizes: "192x192",
      type: "image/png",
      purpose: "any",
    },
    {
      src: abs("/icons/icon-512.png"),
      sizes: "512x512",
      type: "image/png",
      purpose: "any",
    },
    {
      src: abs("/icons/icon-512.png"),
      sizes: "512x512",
      type: "image/png",
      purpose: "maskable",
    },
  ],
};

const outDir = path.join(__dirname, "..", "public");
for (const file of ["manifest.json", "manifest.webmanifest"]) {
  fs.writeFileSync(path.join(outDir, file), `${JSON.stringify(manifest, null, 2)}\n`);
  console.log("wrote", file, "start_url=", manifest.start_url);
}
