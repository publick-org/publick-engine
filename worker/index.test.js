// Run with: node --test worker/index.test.js
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import worker, { MANIFEST_TTL_MS, clearManifests, loadManifest, resolve } from "./index.js";

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
