/* Dubby PWA service worker — same-origin assets only; never touch API host. */
const CACHE = "dubby-v6";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(caches.open(CACHE));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  // Cross-origin (api.dubbyai.com, R2, etc.) must bypass the SW entirely.
  // Intercepting them caused net::ERR_FAILED / fake CORS and
  // "respondWith() was not a Response" when cache miss returned undefined.
  if (url.origin !== self.location.origin) return;

  if (
    url.pathname.endsWith("/api-origin.json") ||
    event.request.mode === "navigate"
  ) {
    event.respondWith(fetch(event.request));
    return;
  }
  event.respondWith(
    fetch(event.request)
      .then((response) => response)
      .catch(async () => {
        const cached = await caches.match(event.request);
        return cached || Response.error();
      }),
  );
});
