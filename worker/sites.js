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
//
// A site in more than one language (English at the root, Spanish under /es/)
// opens its homepage in the language the visitor's browser asks for first:
// / redirects to /es/ for a browser set to Spanish. The language switch on
// every page links with ?lang=<language>, which remembers the choice in a
// cookie, over the browser's, for the homepage. Every other address is served
// as asked, so a shared link opens in the language it was shared in.

export const FORMAT = 1;
// How long a Worker instance reuses a site's manifest before reading it again.
export const MANIFEST_TTL_MS = 60_000;
const IMMUTABLE = "public, max-age=31536000, immutable";
const SHORT = "public, max-age=300";

const manifests = new Map(); // hostname -> {manifest, fetchedAt}
// The cookie that remembers the language a visitor chose with the switch.
export const LANG_COOKIE = "lang";
const YEAR = 365 * 24 * 60 * 60;

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

// The site's languages besides English: its folders with their own home page (es/index.html).
function siteLanguages(files) {
  return Object.keys(files).filter((k) => /^[a-z]{2}\/index\.html$/.test(k)).map((k) => k.slice(0, 2));
}

// The language a visitor asks for, among English and the site's others: the one they chose
// with the switch (the cookie), or else the first of their browser's that the site has.
export function chosenLanguage(request, languages) {
  const offered = ["en", ...languages];
  const cookie = (request.headers.get("Cookie") || "").split(";").map((c) => c.trim().split("="))
    .find(([name]) => name === LANG_COOKIE);
  if (cookie && offered.includes(cookie[1])) return cookie[1];
  const asked = (request.headers.get("Accept-Language") || "").split(",").map((part, i) => {
    const [tag, ...params] = part.trim().split(";");
    const q = params.map((p) => p.trim()).find((p) => p.startsWith("q="));
    return { lang: tag.trim().toLowerCase().split("-")[0], q: q ? Number(q.slice(2)) || 0 : 1, i };
  }).filter((a) => a.q > 0 && offered.includes(a.lang)).sort((a, b) => b.q - a.q || a.i - b.i);
  return asked.length ? asked[0].lang : "en";
}

function redirect(location, headers = {}) {
  return new Response(null, { status: 302, headers: { Location: location, "Cache-Control": "no-store", ...headers } });
}

function cacheControl(key, url) {
  // Engine assets are linked with ?v=<hash of their content>, so that URL never changes meaning.
  if (key.startsWith("static/") && url.searchParams.has("v")) return IMMUTABLE;
  return SHORT;
}

async function serve(request, env, url, key, entry, status, vary = null) {
  const headers = new Headers({
    "Content-Type": entry.type,
    "Cache-Control": status === 200 ? cacheControl(key, url) : "no-store",
    ETag: `"${entry.blob}"`,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
  });
  // A page another language's visitors are redirected from depends on who asks.
  if (vary) headers.set("Vary", vary);
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
  const { key, redirect: canonical } = resolve(manifest.files, url.pathname);
  if (canonical) return Response.redirect(`${url.origin}${canonical}${url.search}`, 301);
  const languages = siteLanguages(manifest.files);
  let vary = null;
  if (languages.length) {
    // The language switch: remember the choice, then show the page without ?lang.
    const choice = url.searchParams.get("lang");
    if (choice !== null) {
      url.searchParams.delete("lang");
      const cookie = choice === "en" || languages.includes(choice)
        ? { "Set-Cookie": `${LANG_COOKIE}=${choice}; Path=/; Max-Age=${YEAR}; SameSite=Lax; Secure` } : {};
      return redirect(`${url.pathname}${url.search}`, cookie);
    }
    // The homepage, for a visitor who asks for another language.
    if (url.pathname === "/") {
      vary = "Accept-Language, Cookie";
      const lang = chosenLanguage(request, languages);
      if (lang !== "en" && manifest.files[`${lang}/index.html`]) return redirect(`/${lang}/${url.search}`, { Vary: vary });
    }
  }
  if (key && manifest.files[key]) return serve(request, env, url, key, manifest.files[key], 200, vary);
  const folder = url.pathname.split("/")[1];
  const notFound = /^[a-z]{2}$/.test(folder) && manifest.files[`${folder}/404.html`] ? `${folder}/404.html` : "404.html";
  const missing = manifest.files[notFound];
  if (missing) return serve(request, env, url, notFound, missing, 404);
  return new Response("Not found", { status: 404 });
}
