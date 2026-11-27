// Офлайн для приложения бригады: страницы /lite и статика берутся из кэша, когда нет связи.
// Запросы к API не кэшируются — фото ждут отправки в IndexedDB.
const CACHE = "umny-lite-v1";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/lite", "/lite/capture"])).catch(() => undefined));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))),
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== self.location.origin || url.pathname.startsWith("/api/")) return;

  if (url.pathname.startsWith("/_next/static/") || url.pathname === "/icon.svg") {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((resp) => {
            const copy = resp.clone();
            void caches.open(CACHE).then((c) => c.put(req, copy));
            return resp;
          }),
      ),
    );
    return;
  }

  if (req.mode === "navigate" && url.pathname.startsWith("/lite")) {
    event.respondWith(
      fetch(req)
        .then((resp) => {
          const copy = resp.clone();
          void caches.open(CACHE).then((c) => c.put(url.pathname, copy));
          return resp;
        })
        .catch(() => caches.match(url.pathname).then((hit) => hit || caches.match("/lite"))),
    );
  }
});
