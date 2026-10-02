// Offline shell and read-only history cache. Writes are never touched here.
const SHELL = "ect-shell-v1";
const DATA = "ect-data-v1";

// Live state, big files or non-history: always go to the network.
const NEVER_CACHE = [/^\/api\/relay\//, /^\/api\/inbox/, /^\/api\/health/, /^\/api\/doctor/, /\/audio$/];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(SHELL).then((cache) => cache.add("/")));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) =>
        Promise.all(names.filter((n) => n !== SHELL && n !== DATA).map((n) => caches.delete(n))),
      )
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (request.mode === "navigate") {
    event.respondWith(navigate(request));
  } else if (url.pathname.startsWith("/api/")) {
    if (NEVER_CACHE.some((rule) => rule.test(url.pathname))) return;
    event.respondWith(networkFirst(request));
  } else if (url.pathname.startsWith("/assets/")) {
    event.respondWith(cacheFirst(request));
  }
});

// Network first, so a signed-out user gets the login redirect. The cached shell is only
// for when the network is really gone.
async function navigate(request) {
  try {
    const response = await fetch(request);
    if (response.ok) {
      const cache = await caches.open(SHELL);
      await cache.put("/", response.clone());
    }
    return response;
  } catch {
    return (await caches.match("/")) ?? Response.error();
  }
}

async function cacheFirst(request) {
  const cached = await caches.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) (await caches.open(SHELL)).put(request, response.clone());
  return response;
}

// Network first, not stale-first: right after a save the page re-reads, and an old copy
// would show the edit as lost (and make the next notes save 409). The cache is the
// fallback for being offline.
async function networkFirst(request) {
  const cache = await caches.open(DATA);
  try {
    const response = await fetch(request);
    // Only a real answer is kept: never an Access redirect or an error page.
    if (response.ok) await cache.put(request, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;
    return new Response(
      JSON.stringify({ detail: "You are offline, and this has not been saved on the phone yet." }),
      { status: 503, headers: { "content-type": "application/json" } },
    );
  }
}
