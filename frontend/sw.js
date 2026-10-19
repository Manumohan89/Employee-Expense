// Tiny service worker: caches static assets so the app shell opens fast and installs as a PWA. API calls are never cached.
const C = "ledgerly-v4", ASSETS = ["css/style.css", "js/app.js", "img/logo.svg"];
self.addEventListener("install", (e) => e.waitUntil(caches.open(C).then((c) => c.addAll(ASSETS))));
self.addEventListener("activate", (e) => e.waitUntil(caches.keys().then((k) => Promise.all(k.filter((x) => x !== C).map((x) => caches.delete(x))))));
self.addEventListener("fetch", (e) => {
  const u = new URL(e.request.url);
  if (e.request.method === "GET" && u.origin === location.origin && ASSETS.some((a) => u.pathname.endsWith(a)))
    e.respondWith(fetch(e.request).then((r) => { caches.open(C).then((c) => c.put(e.request, r.clone())); return r; }).catch(() => caches.match(e.request)));
});
