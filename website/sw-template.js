const root = new URL("./", self.location.href);
const cacheName = "videodeepread-shell:" + root.pathname + ":__SHELL_VERSION__";
const cachePrefix = "videodeepread-shell:" + root.pathname + ":";
const shell = __SHELL_FILES__.map(path => new URL(path, root).href);
const index = new URL("index.html", root).href;

self.addEventListener("install", event => {
    event.waitUntil((async () => {
        const cache = await caches.open(cacheName);
        await cache.addAll(shell.map(url => new Request(url, { cache: "reload" })));
    })());
});

self.addEventListener("activate", event => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(keys.filter(key => key.startsWith(cachePrefix) && key !== cacheName)
            .map(key => caches.delete(key)));
        await self.clients.claim();
    })());
});

self.addEventListener("fetch", event => {
    const request = event.request;
    if (request.method !== "GET") return;
    const url = new URL(request.url);
    if (url.origin !== root.origin || !url.pathname.startsWith(root.pathname)) return;

    if (request.mode === "navigate" && (url.pathname === root.pathname || url.pathname === new URL(index).pathname)) {
        event.respondWith((async () => {
            try {
                const response = await fetch(request);
                if (response.ok) return response;
            } catch {
                // Use the matching installed app shell when the network is unavailable.
            }
            return (await caches.open(cacheName)).match(index);
        })());
    } else if (shell.includes(url.href)) {
        event.respondWith((async () => (await (await caches.open(cacheName)).match(request)) || fetch(request))());
    }
});
