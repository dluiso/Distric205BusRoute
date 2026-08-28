'use strict';

const CACHE_NAME = 'd205-bus-portal-v3';
const OFFLINE_URL = '/offline';
const PRECACHE = [
  OFFLINE_URL,
  '/manifest.webmanifest',
  '/static/css/public.css',
  '/static/js/public_portal.js',
  '/static/icons/bus-route.svg',
  '/static/icons/bus-route-192.png',
  '/static/icons/bus-route-512.png',
  '/static/vendor/fontawesome/css/all.min.css',
  '/static/vendor/fontawesome/webfonts/fa-solid-900.woff2',
];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(PRECACHE)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  // Live bus data must never fall back to a stale cached API response.
  if (url.pathname.startsWith('/api/')) return;

  if (request.mode === 'navigate') {
    if (url.pathname !== '/') return;
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
    return;
  }

  if (url.pathname.startsWith('/static/') || url.pathname === '/manifest.webmanifest') {
    event.respondWith(
      caches.match(request).then((cached) => {
        const network = fetch(request).then((response) => {
          if (response.ok) {
            const copy = response.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, copy));
          }
          return response;
        });
        return cached || network;
      }),
    );
  }
});
