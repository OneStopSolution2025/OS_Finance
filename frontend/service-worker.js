// Bumped on every meaningful change to force old caches to be discarded —
// this alone doesn't fix staleness though; see the fetch strategy below.
const CACHE_NAME = 'os-finances-v2';
const SHELL_FILES = ['/', '/index.html', '/manifest.json'];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(SHELL_FILES))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

// Network-first for the app shell too, not just API calls. A cache-first shell
// means every code fix silently never reaches anyone whose browser already
// cached the old version — the cache becomes a permanent trap for old bugs.
// Only fall back to cache when the network genuinely fails (offline).
self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET') return; // never cache mutating requests

  if (SHELL_FILES.some((f) => url.pathname === f || url.pathname.endsWith(f))) {
    event.respondWith(
      fetch(event.request)
        .then((response) => {
          const clone = response.clone();
          caches.open(CACHE_NAME).then((cache) => cache.put(event.request, clone));
          return response;
        })
        .catch(() => caches.match(event.request))
    );
  }
});
