// How the network's Worker (index.js) serves every Publick site from the
// sites bucket (see pipeline/deploy.py). Kept apart from index.js, whose
// module may export only the Worker's handler.
//
// The site is chosen by the request's hostname: gloucester-ma.publick.org is
// read from sites/gloucester-ma.publick.org/current.json, a manifest mapping
// each path to a blob. Paths resolve as GitHub Pages resolved them: /about/
// serves about/index.html, /about redirects to /about/, and anything unknown
// gets the site's 404.html with status 404: its language's, for a path under a
// language's folder (/es/... gets es/404.html).

export const FORMAT = 1;
// How long a Worker instance reuses a site's manifest before reading it again.
export const MANIFEST_TTL_MS = 60_000;
const IMMUTABLE = "public, max-age=31536000, immutable";
const SHORT = "public, max-age=300";

const manifests = new Map(); // hostname -> {manifest, fetchedAt}

export async function loadManifest(env, host, now = Date.now()) {
  const cached = manifests.get(host);
  if (cached && now - cached.fetchedAt < MANIFEST_TTL_MS) return cached.manifest;
  const object = await env.SITES.get(`sites/${host}/current.json`);
  const manifest = object ? await object.json() : null;
  if (manifest && manifest.format !== FORMAT) {
    throw new Error(`${host}: manifest format ${manifest.format}, this Worker reads ${FORMAT}`);
  }
  manifests.set(host, { manifest, fetchedAt: now });
  return manifest;
}

export function clearManifests() {
  manifests.clear();
}

// The manifest entry for a URL path, or a redirect to its canonical form.
export function resolve(files, pathname) {
  let path;
  try {
    path = decodeURIComponent(pathname);
  } catch {
    return {};
  }
  if (path.includes("\0")) return {};
  const rel = path.replace(/^\/+/, "");
  if (rel === "" || rel.endsWith("/")) return { key: `${rel}index.html` };
  if (files[rel]) return { key: rel };
  if (files[`${rel}/index.html`]) return { redirect: `${pathname}/` };
  if (files[`${rel}.html`]) return { key: `${rel}.html` };
  return {};
}

function cacheControl(key, url) {
  // Engine assets are linked with ?v=<hash of their content>, so that URL never changes meaning.
  if (key.startsWith("static/") && url.searchParams.has("v")) return IMMUTABLE;
  return SHORT;
}

async function serve(request, env, url, key, entry, status) {
  const headers = new Headers({
    "Content-Type": entry.type,
    "Cache-Control": status === 200 ? cacheControl(key, url) : "no-store",
    ETag: `"${entry.blob}"`,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
  });
  if (status === 200 && request.headers.get("If-None-Match") === `"${entry.blob}"`) {
    return new Response(null, { status: 304, headers });
  }
  headers.set("Content-Length", String(entry.size));
  if (request.method === "HEAD") return new Response(null, { status, headers });
  const object = await env.SITES.get(`blobs/${entry.blob}`);
  if (!object) return new Response("This page is temporarily unavailable.", { status: 503 });
  return new Response(object.body, { status, headers });
}

export async function handle(request, env) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405, headers: { Allow: "GET, HEAD" } });
  }
  const url = new URL(request.url);
  const host = url.hostname.toLowerCase();
  if (host.startsWith("www.")) {
    return Response.redirect(`${url.protocol}//${host.slice(4)}${url.pathname}${url.search}`, 301);
  }
  const manifest = await loadManifest(env, host);
  if (!manifest) return new Response("No site here.", { status: 404 });
  const { key, redirect } = resolve(manifest.files, url.pathname);
  if (redirect) return Response.redirect(`${url.origin}${redirect}${url.search}`, 301);
  if (key && manifest.files[key]) return serve(request, env, url, key, manifest.files[key], 200);
  const folder = url.pathname.split("/")[1];
  const notFound = /^[a-z]{2}$/.test(folder) && manifest.files[`${folder}/404.html`] ? `${folder}/404.html` : "404.html";
  const missing = manifest.files[notFound];
  if (missing) return serve(request, env, url, notFound, missing, 404);
  return new Response("Not found", { status: 404 });
}
