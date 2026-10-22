// Muscle Track service worker.
// - static files (app code, Bootstrap, Chart.js...), the translation catalog and the exercise thumbnails:
//   stale-while-revalidate (fast, works with a weak signal)
// - pages: always network; when offline, show /offline/
// - a student's own main screens (today, workouts, agenda, package, history) and the screens of a workout (in progress,
//   or to start one with no connection) are the only logged-in pages kept: so the app opens, and a workout can be reopened
//   or started, in a gym with no signal. They are fetched again whenever there is a connection, and dropped when the
//   workout ends, when the student logs out and after their TTL. Nothing of a trainer is ever kept (the marker
//   data-student-app only exists on a student's pages). The workout itself lives in the page (static/js/workout.js).
// Offline set updates are queued by the page itself (static/js/workout.js).
const CACHE = 'muscle-track-v5'; // bump to drop old caches (v1 also cached CDN files)
const WORKOUT_CACHE = 'muscle-track-workout-v1';
const WORKOUT_TTL = 12 * 60 * 60 * 1000;
const PAGE_TTL = 3 * 24 * 60 * 60 * 1000;   // the main screens may be a little old offline (the page says when the copy is from)
const SLOW_NETWORK_MS = 4000;   // a weak signal must not hold a student up: after this the kept screen is used
const WORKOUT_PAGE = /^\/my-workouts\/\d+\/(execute|offline)\/$/;
const STUDENT_PAGE = /^\/(my-workouts\/|my-agenda\/|my-package\/|my-history\/)?$/;   // only the plain address, never with a query
const OFFLINE_URL = '/offline/';

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.add(OFFLINE_URL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE && k !== WORKOUT_CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isCacheableAsset(url) {
  return url.origin === self.location.origin &&
    (url.pathname.startsWith('/static/') || url.pathname === '/jsi18n/' || url.pathname.startsWith('/media/exercises/'));
}

// The kept copy is served marked, so the page knows it may be older than the device's own state and restores from it
async function keptCopy(cached, key) {
  const at = cached.headers.get('X-Cached-At') || '0';
  let html = await cached.text();
  if (WORKOUT_PAGE.test(key)) html = html.replace('data-workout-page', 'data-workout-page data-offline-copy="1"');
  html = html.replace('<body', '<body data-cached-at="' + at + '"');
  return new Response(html, { headers: { 'Content-Type': 'text/html; charset=utf-8' } });
}

async function keptPage(event) {
  const request = event.request;
  const cache = await caches.open(WORKOUT_CACHE);
  const key = new URL(request.url).pathname;
  const ttl = WORKOUT_PAGE.test(key) ? WORKOUT_TTL : PAGE_TTL;
  const network = fetch(request).then(async (response) => {
    const isPage = (response.headers.get('Content-Type') || '').includes('text/html');
    const body = response.ok && !response.redirected && isPage ? await response.clone().text() : null;
    if (body !== null && body.includes('data-student-app')) {
      await cache.put(key, new Response(body, {
        headers: { 'Content-Type': 'text/html; charset=utf-8', 'X-Cached-At': String(Date.now()) },
      }));
    } else if (response.redirected || response.status === 403 || response.status === 404 || body !== null) {
      await cache.delete(key);   // gone (finished, logged out, not this student's): do not bring it back offline
    }
    return response;
  });
  event.waitUntil(network.catch(() => {}));   // keep refreshing the copy even if the kept one was used meanwhile
  const cached = await cache.match(key);
  const fresh = cached && Date.now() - Number(cached.headers.get('X-Cached-At') || 0) < ttl;
  if (!fresh) {
    if (cached) await cache.delete(key);
    return network.catch(() => caches.match(OFFLINE_URL));
  }
  try {
    return await Promise.race([network, new Promise((_, reject) => setTimeout(reject, SLOW_NETWORK_MS))]);
  } catch (error) {
    return keptCopy(cached, key);
  }
}

self.addEventListener('fetch', (event) => {
  const request = event.request;
  const url = new URL(request.url);

  if (request.method === 'POST' && url.pathname.endsWith('/accounts/logout/')) {
    event.waitUntil(caches.delete(WORKOUT_CACHE));   // the next person on this phone must not find the last workout
    return;
  }
  if (request.method !== 'GET') return;

  const isKept = url.origin === self.location.origin &&
    (WORKOUT_PAGE.test(url.pathname) || (STUDENT_PAGE.test(url.pathname) && !url.search));
  if (isKept && (request.mode === 'navigate' || request.headers.get('X-Workout-Prefetch'))) {
    event.respondWith(keptPage(event));
    return;
  }

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

// Web push (core/notifications.py sends {title, body, url, tag})
self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (e) { data = { body: event.data ? event.data.text() : '' }; }
  event.waitUntil(Promise.all([
    self.registration.showNotification(data.title || 'Muscle Track', {
      body: data.body || '',
      icon: '/static/images/icon-192.png',
      badge: '/static/images/icon-192.png',
      tag: data.tag || undefined,
      data: { url: data.url || '/' },
    }),
    // a dot on the app icon until the next page load puts the real number there
    (self.navigator && self.navigator.setAppBadge ? self.navigator.setAppBadge() : Promise.resolve()).catch(() => {}),
  ]));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL((event.notification.data && event.notification.data.url) || '/', self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((windows) => {
      for (const client of windows) {
        if ('focus' in client) {
          if ('navigate' in client) client.navigate(target);
          return client.focus();
        }
      }
      return self.clients.openWindow(target);
    })
  );
});
