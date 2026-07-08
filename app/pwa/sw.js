/* 360Five service worker — offline shell + fresh-when-online data.
   Pages bake the slate in at build time (no runtime data fetch), so caching the
   HTML pages is enough for offline. Bump CACHE to force a refresh on redeploy. */
const CACHE = '360five-v2';
const CORE = [
  './index.html', './plays.html', './record.html',
  './manifest.webmanifest',
  './icon-192.png', './icon-512.png', './apple-touch-icon.png',
];

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches.open(CACHE)
      // cache core assets individually so one 404 can't fail the whole install
      .then((c) => Promise.all(CORE.map((u) => c.add(u).catch(() => null))))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;   // leave cross-origin alone

  const isPage = req.mode === 'navigate' || req.destination === 'document';
  if (isPage) {
    // network-first: fresh hourly data online, last-seen page offline
    e.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(req, copy));
          return res;
        })
        .catch(() => caches.match(req).then((r) => r || caches.match('./index.html')))
    );
  } else {
    // static assets: cache-first
    e.respondWith(
      caches.match(req).then((r) => r || fetch(req).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
        return res;
      }))
    );
  }
});
