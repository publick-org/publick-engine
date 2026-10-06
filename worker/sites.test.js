// Run with: node --test worker/sites.test.js
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker from "./index.js";
import { MANIFEST_TTL_MS, clearManifests, loadManifest, resolve } from "./sites.js";

function entry(blob, type = "text/html; charset=utf-8") {
  return { blob, type, size: `content ${blob}`.length };
}

const MANIFEST = {
  format: 1,
  domain: "t.publick.org",
  build: "20261002T110000Z-abc",
  files: {
    "index.html": entry("home"),
    "about/index.html": entry("about"),
    "404.html": entry("missing"),
    "es/index.html": entry("inicio"),
    "es/404.html": entry("no-encontrada"),
    "es/about/index.html": entry("acerca"),
    "feed.xml": entry("feed", "application/xml; charset=utf-8"),
    "static/css/site.css": entry("css", "text/css; charset=utf-8"),
    "meetings/data/decisions.csv": entry("csv", "text/csv; charset=utf-8"),
  },
};

function bucket(objects) {
  const reads = [];
  return {
    reads,
    async get(key) {
      reads.push(key);
      if (!(key in objects)) return null;
      const value = objects[key];
      return { body: typeof value === "string" ? value : JSON.stringify(value), json: async () => value };
    },
  };
}

function env(manifest = MANIFEST) {
  const objects = { "sites/t.publick.org/current.json": manifest };
  for (const e of Object.values(manifest.files)) objects[`blobs/${e.blob}`] = `content ${e.blob}`;
  return { SITES: bucket(objects) };
}

const get = (path, options = {}) => new Request(`https://t.publick.org${path}`, options);

beforeEach(clearManifests);

test("serves pages by clean URL", async () => {
  const e = env();
  for (const [path, body] of [["/", "home"], ["/about/", "about"], ["/feed.xml", "feed"]]) {
    const response = await worker.fetch(get(path), e);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), `content ${body}`);
  }
  const csv = await worker.fetch(get("/meetings/data/decisions.csv"), e);
  assert.equal(csv.headers.get("Content-Type"), "text/csv; charset=utf-8");
  assert.equal(csv.headers.get("ETag"), '"csv"');
});

test("asks browsers for HTTPS only, for a year, on this host alone", async () => {
  const e = env();
  for (const path of ["/", "/feed.xml", "/no-such-page/"]) {
    const response = await worker.fetch(get(path), e);
    assert.equal(response.headers.get("Strict-Transport-Security"), "max-age=31536000", path);
  }
  const chose = await worker.fetch(get("/?lang=en"), e);
  assert.equal(chose.headers.get("Strict-Transport-Security"), "max-age=31536000");
});

test("redirects a folder without its slash, keeping the query", async () => {
  const response = await worker.fetch(get("/about?x=1"), env());
  assert.equal(response.status, 301);
  assert.equal(response.headers.get("Location"), "https://t.publick.org/about/?x=1");
});

test("unknown paths get the site's 404 page", async () => {
  for (const path of ["/nope", "/nope/", "/about/extra", "/%E0%A4%A", "/..%2f..%2fetc/passwd"]) {
    const response = await worker.fetch(get(path), env());
    assert.equal(response.status, 404, path);
    assert.equal(await response.text(), "content missing");
    assert.equal(response.headers.get("Cache-Control"), "no-store");
  }
});

test("unknown paths in a language's folder get that language's 404 page", async () => {
  for (const path of ["/es/nope/", "/es/about/extra"]) {
    const response = await worker.fetch(get(path), env());
    assert.equal(response.status, 404, path);
    assert.equal(await response.text(), "content no-encontrada");
  }
  // A folder that isn't a language's, or a language the site doesn't have, gets the English page.
  for (const path of ["/fr/nope/", "/esx/nope/"]) {
    assert.equal(await (await worker.fetch(get(path), env())).text(), "content missing", path);
  }
});

test("the homepage opens in the language the browser asks for first", async () => {
  const spanish = { "Accept-Language": "es-US,es;q=0.9,en;q=0.8" };
  const response = await worker.fetch(get("/?x=1", { headers: spanish }), env());
  assert.equal(response.status, 302);
  assert.equal(response.headers.get("Location"), "/es/?x=1");
  assert.equal(response.headers.get("Vary"), "Accept-Language, Cookie");
  // English first, or a language the site doesn't have: the English homepage.
  for (const asked of ["en-US,en;q=0.9,es;q=0.8", "fr-FR,fr;q=0.9", "", "es;q=0"]) {
    const page = await worker.fetch(get("/", { headers: { "Accept-Language": asked } }), env());
    assert.equal(page.status, 200, asked);
    assert.equal(await page.text(), "content home");
    assert.equal(page.headers.get("Vary"), "Accept-Language, Cookie");
  }
});

test("any other address opens as asked, so a shared link keeps its language", async () => {
  const spanish = { "Accept-Language": "es", Cookie: "lang=es" };
  const about = await worker.fetch(get("/about/", { headers: spanish }), env());
  assert.equal(about.status, 200);
  assert.equal(await about.text(), "content about");
  assert.equal(about.headers.get("Vary"), null);
  assert.equal((await worker.fetch(get("/feed.xml", { headers: spanish }), env())).status, 200);
  const es = await worker.fetch(get("/es/about/", { headers: { "Accept-Language": "en", Cookie: "lang=en" } }), env());
  assert.equal(await es.text(), "content acerca");
});

test("the language switch is remembered over the browser's language", async () => {
  const chose = await worker.fetch(get("/about/?lang=en"), env());
  assert.equal(chose.status, 302);
  assert.equal(chose.headers.get("Location"), "/about/");
  assert.match(chose.headers.get("Set-Cookie"), /^lang=en; Path=\/; Max-Age=\d+; SameSite=Lax; Secure$/);
  const english = await worker.fetch(get("/", { headers: { "Accept-Language": "es", Cookie: "a=1; lang=en" } }), env());
  assert.equal(await english.text(), "content home");
  const spanish = await worker.fetch(get("/", { headers: { "Accept-Language": "en", Cookie: "lang=es" } }), env());
  assert.equal(spanish.headers.get("Location"), "/es/");
  // A language the site doesn't have isn't remembered.
  const unknown = await worker.fetch(get("/about/?lang=fr"), env());
  assert.equal(unknown.headers.get("Set-Cookie"), null);
});

test("the language switch never redirects to another site", async () => {
  for (const path of ["//example.com/?lang=es", "///example.com/?lang=es", "/\\example.com/?lang=es"]) {
    const response = await worker.fetch(get(path), env());
    assert.equal(response.status, 302);
    assert.equal(response.headers.get("Location"), "/example.com/", path);
  }
});

test("a site in English only ignores the browser's language", async () => {
  const files = Object.fromEntries(Object.entries(MANIFEST.files).filter(([k]) => !k.startsWith("es/")));
  const page = await worker.fetch(get("/", { headers: { "Accept-Language": "es" } }), env({ ...MANIFEST, files }));
  assert.equal(page.status, 200);
  assert.equal(page.headers.get("Vary"), null);
});

test("an unknown hostname has no site", async () => {
  const response = await worker.fetch(new Request("https://other.publick.org/"), env());
  assert.equal(response.status, 404);
});

test("versioned assets are cached for good, everything else briefly", async () => {
  const e = env();
  const versioned = await worker.fetch(get("/static/css/site.css?v=4d569b50fe"), e);
  assert.match(versioned.headers.get("Cache-Control"), /immutable/);
  const plain = await worker.fetch(get("/static/css/site.css"), e);
  assert.equal(plain.headers.get("Cache-Control"), "public, max-age=300");
  const page = await worker.fetch(get("/"), e);
  assert.equal(page.headers.get("Cache-Control"), "public, max-age=300");
});

test("HEAD and conditional requests read no file", async () => {
  const e = env();
  const head = await worker.fetch(get("/about/", { method: "HEAD" }), e);
  assert.equal(head.status, 200);
  assert.equal(head.headers.get("Content-Length"), String("content about".length));
  const cached = await worker.fetch(get("/about/", { headers: { "If-None-Match": '"about"' } }), e);
  assert.equal(cached.status, 304);
  assert.deepEqual(e.SITES.reads.filter((k) => k.startsWith("blobs/")), []);
});

test("only GET and HEAD are allowed", async () => {
  const response = await worker.fetch(get("/", { method: "POST" }), env());
  assert.equal(response.status, 405);
});

test("www redirects to the bare hostname", async () => {
  const response = await worker.fetch(new Request("https://www.publick.org/about/"), env());
  assert.equal(response.status, 301);
  assert.equal(response.headers.get("Location"), "https://publick.org/about/");
});

test("a missing file is an outage, not a 404", async () => {
  const e = env();
  delete e.SITES;
  e.SITES = bucket({ "sites/t.publick.org/current.json": MANIFEST });
  const response = await worker.fetch(get("/"), e);
  assert.equal(response.status, 503);
});

test("the manifest is reread after its time to live", async () => {
  const e = env();
  await loadManifest(e, "t.publick.org", 0);
  await loadManifest(e, "t.publick.org", MANIFEST_TTL_MS - 1);
  await loadManifest(e, "t.publick.org", MANIFEST_TTL_MS + 1);
  assert.equal(e.SITES.reads.length, 2);
});

test("a manifest in another format is refused", async () => {
  await assert.rejects(loadManifest(env({ ...MANIFEST, format: 2 }), "t.publick.org"), /format 2/);
});

test("resolve", () => {
  const files = MANIFEST.files;
  assert.deepEqual(resolve(files, "/"), { key: "index.html" });
  assert.deepEqual(resolve(files, "/about"), { redirect: "/about/" });
  assert.deepEqual(resolve(files, "/feed.xml"), { key: "feed.xml" });
  assert.deepEqual(resolve(files, "/%zz"), {});
});

test("the Worker's module exports only its handler", async () => {
  // Workers treat every export of the main module as an entry point, and refuse anything else.
  const module = await import("./index.js");
  assert.deepEqual(Object.keys(module), ["default"]);
  assert.equal(typeof module.default.fetch, "function");
});

// ---- The weekly digest's signup (digest.js) ----

const SIGNUP_MANIFEST = { ...MANIFEST, files: { ...MANIFEST.files, "digest/thanks/index.html": entry("thanks") } };

// Buttondown's API, recorded: each call's method, path, headers and body; answers from `answers` ("METHOD path" -> [status, body]).
function fakeButtondown(answers) {
  const calls = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const path = url.replace("https://api.buttondown.com/v1", "");
    const method = init.method || "GET";
    calls.push({ method, path, headers: init.headers, body: init.body ? JSON.parse(init.body) : null });
    const [status, body] = answers[`${method} ${path}`] || [500, { detail: "not expected" }];
    return new Response(JSON.stringify(body), { status });
  };
  return { calls, restore: () => { globalThis.fetch = realFetch; } };
}

function signup(fields, headers = { Origin: "https://t.publick.org" }) {
  return new Request("https://t.publick.org/digest/subscribe", {
    method: "POST",
    body: new URLSearchParams(fields),
    headers: { "Content-Type": "application/x-www-form-urlencoded", "CF-Connecting-IP": "203.0.113.9",
               Referer: "https://t.publick.org/digest/", ...headers },
  });
}

async function signUp(answers, fields = { email: "reader@example.org" }, headers = undefined, withKey = true) {
  const fake = fakeButtondown(answers);
  try {
    const e = { ...env(SIGNUP_MANIFEST), ...(withKey ? { BUTTONDOWN_SUBSCRIBE_KEY: "subscribe-key" } : {}) };
    const response = await worker.fetch(signup(fields, headers), e);
    return { response, calls: fake.calls };
  } finally {
    fake.restore();
  }
}

const LOOKUP = "GET /subscribers/reader%40example.org";

test("a new address is added with its town's tag, unconfirmed, and told to check its email", async () => {
  const { response, calls } = await signUp({ [LOOKUP]: [404, {}], "POST /subscribers": [201, {}] });
  assert.equal(response.status, 303);
  assert.equal(response.headers.get("Location"), "https://t.publick.org/digest/thanks/");
  assert.equal(calls.length, 2);
  assert.equal(calls[0].headers.Authorization, "Token subscribe-key");
  // No type and no collision behavior: Buttondown's default, which asks the address to confirm.
  assert.deepEqual(calls[1].body, { email_address: "reader@example.org", tags: ["t-en"],
                                    referrer_url: "https://t.publick.org/digest/", ip_address: "203.0.113.9" });
  assert.equal(calls[1].headers["X-Buttondown-Collision-Behavior"], undefined);
});

test("an address already confirmed gets the town's tag, added to its others", async () => {
  const { response, calls } = await signUp({ [LOOKUP]: [200, { type: "regular", tags: ["u-en"] }], "POST /subscribers": [201, {}] });
  assert.equal(response.headers.get("Location"), "https://t.publick.org/digest/thanks/");
  assert.equal(calls[1].headers["X-Buttondown-Collision-Behavior"], "add");
  assert.deepEqual(calls[1].body.tags, ["t-en"]);
  assert.equal(calls[1].body.type, undefined);
});

test("an address that unsubscribed, or never confirmed, is asked to confirm again", async () => {
  for (const type of ["unsubscribed", "unactivated"]) {
    const { calls } = await signUp({ [LOOKUP]: [200, { type }], "POST /subscribers": [201, {}] });
    assert.equal(calls[1].headers["X-Buttondown-Collision-Behavior"], "add");
    assert.equal(calls[1].body.type, "unactivated", type);
  }
});

test("an address Buttondown won't send to is left alone, and answered the same way", async () => {
  for (const type of ["blocked", "complained", "undeliverable"]) {
    const { response, calls } = await signUp({ [LOOKUP]: [200, { type }] });
    assert.equal(response.headers.get("Location"), "https://t.publick.org/digest/thanks/");
    assert.equal(calls.length, 1, type);
  }
});

test("a bot that fills in the hidden field is answered as if done, and nothing is sent", async () => {
  const { response, calls } = await signUp({}, { email: "bot@example.org", website: "https://spam.example" });
  assert.equal(response.headers.get("Location"), "https://t.publick.org/digest/thanks/");
  assert.equal(calls.length, 0);
});

test("an address that isn't one, a missing key, or Buttondown failing: the 'didn't work' page", async () => {
  for (const email of ["", "not an address", `${"a".repeat(250)}@example.org`]) {
    const { response, calls } = await signUp({}, { email });
    assert.equal(response.headers.get("Location"), "https://t.publick.org/digest/problem/", email);
    assert.equal(calls.length, 0);
  }
  const { response: noKey, calls: none } = await signUp({}, undefined, undefined, false);
  assert.equal(noKey.headers.get("Location"), "https://t.publick.org/digest/problem/");
  assert.equal(none.length, 0);
  const logged = [];
  const realError = console.error;
  console.error = (message) => logged.push(message);
  try {
    const { response: failed } = await signUp({ [LOOKUP]: [404, {}],
      "POST /subscribers": [400, { code: "email_invalid", detail: "reader@example.org is not valid" }] });
    assert.equal(failed.headers.get("Location"), "https://t.publick.org/digest/problem/");
  } finally {
    console.error = realError;
  }
  // The Worker's logs say what failed, never whose address it was.
  assert.deepEqual(logged, ["Digest signup on t.publick.org: adding a subscriber: HTTP 400 email_invalid"]);
});

test("only the town's own pages can sign someone up", async () => {
  for (const origin of ["https://evil.example", "https://u.publick.org", "null"]) {
    const { response, calls } = await signUp({}, undefined, { Origin: origin });
    assert.equal(response.status, 403, origin);
    assert.equal(calls.length, 0);
  }
});

test("a site without the digest's signup takes no POST", async () => {
  const response = await worker.fetch(signup({ email: "reader@example.org" }), env());
  assert.equal(response.status, 405);
  const other = await worker.fetch(new Request("https://t.publick.org/digest/", { method: "POST" }), env(SIGNUP_MANIFEST));
  assert.equal(other.status, 405);
});
