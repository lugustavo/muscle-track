// Muscle Track service worker.
// - static files (app code, Bootstrap, Chart.js...): stale-while-revalidate (fast, works with a weak signal)
// - pages: always network; when offline, show /offline/
// Authenticated pages are deliberately NOT cached (shared phones / after logout).
// Offline set updates are queued by the page itself (static/js/workout.js).
const CACHE = 'muscle-track-v2'; // bump to drop old caches (v1 also cached CDN files)
const OFFLINE_URL = '/offline/';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isCacheableAsset(url) {
  return url.origin === self.location.origin && url.pathname.startsWith('/static/');
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);

  if (request.mode === 'navigate') {
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (isCacheableAsset(url)) {
    event.respondWith(
      caches.open(CACHE).then((cache) =>
        cache.match(request).then((cached) => {
          const network = fetch(request)
            .then((response) => {
              if (response && response.ok) cache.put(request, response.clone());
              return response;
            })
            .catch(() => cached);
          return cached || network;
        })
      )
    );
  }
});
