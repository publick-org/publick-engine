// Submits each site's sitemap to Google Search Console, through its API
// (https://developers.google.com/webmaster-tools/v1/sitemaps), so a new town
// is crawled without waiting for Google to reread the network's sitemap index.
//
// The scheduler (scheduler.js) runs submitSitemaps with each daily run's start.
// It reads the network's sitemap index (SITEMAP_URL, publick.org/sitemap.xml,
// which lists each live town from its first build), asks Search Console which
// sitemaps it already has, and submits the index and each sitemap it lists
// that Search Console doesn't. A sitemap already submitted is left alone:
// Google rereads those on its own. A town first built by a daily run is
// submitted by that morning's next start, an hour later.
//
// Environment: GSC_SERVICE_ACCOUNT_JSON, a secret: the JSON key of a Google
// service account that is a user of the Search Console property, with Full
// permission; without it, nothing is submitted. SEARCH_CONSOLE_SITE, the
// property (default "sc-domain:publick.org"); SITEMAP_URL and SITES, as the
// scheduler has them.

export const SEARCH_CONSOLE = "https://searchconsole.googleapis.com/webmasters/v3";
export const SCOPE = "https://www.googleapis.com/auth/webmasters";
const DEFAULT_SITE = "sc-domain:publick.org";

function base64url(bytes) {
  return btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

const encodeJson = (value) => base64url(new TextEncoder().encode(JSON.stringify(value)));

// The service account's private key (PEM, PKCS #8) for signing.
async function signingKey(pem) {
  const body = pem.replace(/-----(BEGIN|END) PRIVATE KEY-----/g, "").replace(/\s+/g, "");
  const der = Uint8Array.from(atob(body), (c) => c.charCodeAt(0));
  return crypto.subtle.importKey("pkcs8", der, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, ["sign"]);
}

// A signed JWT asking for an access token (https://developers.google.com/identity/protocols/oauth2/service-account).
export async function assertion(account, now = new Date()) {
  const iat = Math.floor(now.getTime() / 1000);
  const unsigned = `${encodeJson({ alg: "RS256", typ: "JWT" })}.` +
    encodeJson({ iss: account.client_email, scope: SCOPE, aud: account.token_uri, iat, exp: iat + 3600 });
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", await signingKey(account.private_key),
    new TextEncoder().encode(unsigned));
  return `${unsigned}.${base64url(signature)}`;
}

async function ok(response, what) {
  if (!response.ok) throw new Error(`${what}: HTTP ${response.status} ${await response.text()}`);
  return response;
}

export async function accessToken(account, now = new Date()) {
  const response = await ok(await fetch(account.token_uri, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({
      grant_type: "urn:ietf:params:oauth:grant-type:jwt-bearer",
      assertion: await assertion(account, now),
    }),
  }), "getting a Google access token");
  return (await response.json()).access_token;
}

// The network's sitemap index, and each sitemap it lists. Throws if it can't be read.
export async function networkSitemaps(env) {
  const index = env.SITEMAP_URL || "https://publick.org/sitemap.xml";
  const response = await env.SITES.fetch(index);
  if (!response.ok) throw new Error(`${index}: HTTP ${response.status}`);
  const listed = [...(await response.text()).matchAll(/<loc>\s*(https:\/\/[^<\s]+)\s*<\/loc>/g)].map((m) => m[1]);
  return [index, ...new Set(listed.filter((url) => url !== index))];
}

// Submits each of the network's sitemaps Search Console doesn't have yet: {submitted: [url]}.
export async function submitSitemaps(env, now = new Date()) {
  if (!env.GSC_SERVICE_ACCOUNT_JSON) return { submitted: [], skipped: "GSC_SERVICE_ACCOUNT_JSON isn't set" };
  const sitemaps = await networkSitemaps(env);
  const token = await accessToken(JSON.parse(env.GSC_SERVICE_ACCOUNT_JSON), now);
  const site = `${SEARCH_CONSOLE}/sites/${encodeURIComponent(env.SEARCH_CONSOLE_SITE || DEFAULT_SITE)}/sitemaps`;
  const headers = { Authorization: `Bearer ${token}` };
  const known = new Set(((await (await ok(await fetch(site, { headers }), "listing Search Console's sitemaps"))
    .json()).sitemap || []).map((s) => s.path));
  const submitted = [];
  for (const url of sitemaps.filter((u) => !known.has(u))) {
    await ok(await fetch(`${site}/${encodeURIComponent(url)}`, { method: "PUT", headers }), `submitting ${url}`);
    submitted.push(url);
  }
  return { submitted };
}
