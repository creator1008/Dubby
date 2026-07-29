/**
 * Write public/manifest.json with absolute paths when building for GitHub Pages.
 * Relative icon/start_url paths are unreliable for some Android Chrome installs
 * under a subdirectory basePath.
 */
const fs = require("fs");
const path = require("path");

const base = process.env.GITHUB_PAGES === "true" ? "/Dubby" : "";
const root = (p) => `${base}${p}`;

const manifest = {
  name: "Dubby",
  short_name: "Dubby",
  description: "AI 영상 다국어 더빙",
  start_url: root("/"),
  scope: root("/"),
  id: root("/"),
  display: "standalone",
  orientation: "any",
  background_color: "#0F9C8A",
  theme_color: "#0F9C8A",
  lang: "ko",
  prefer_related_applications: false,
  icons: [
    {
      src: root("/icons/icon-192.png"),
      sizes: "192x192",
      type: "image/png",
      purpose: "any",
    },
    {
      src: root("/icons/icon-512.png"),
      sizes: "512x512",
      type: "image/png",
      purpose: "any",
    },
    {
      src: root("/icons/icon-512.png"),
      sizes: "512x512",
      type: "image/png",
      purpose: "maskable",
    },
  ],
};

const outDir = path.join(__dirname, "..", "public");
for (const file of ["manifest.json", "manifest.webmanifest"]) {
  fs.writeFileSync(path.join(outDir, file), `${JSON.stringify(manifest, null, 2)}\n`);
  console.log("wrote", file, "base=", base || "(root)");
}
