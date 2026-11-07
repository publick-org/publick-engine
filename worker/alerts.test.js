// Run with: node --test worker/alerts.test.js
// The alerts' SQL runs against Node's own SQLite (node:sqlite), behind the few calls of D1's API alerts.js uses.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DatabaseSync } from "node:sqlite";
import { beforeEach, test } from "node:test";

import worker from "./index.js";
import {
  CONFIRMS_PER_DAY, MAX_FOLLOWS, UNCONFIRMED_DAYS, confirm, follow, hashed, manage, readerToken, send, slug, streetLabel, unsubscribe,
} from "./alerts.js";
import { clearManifests } from "./sites.js";

const SCHEMA = readFileSync(new URL("./alerts.sql", import.meta.url), "utf8");
const ORIGIN = "https://salem-ma.publick.org";
const HOOK = "hook-secret";

class Statement {
  constructor(db, sql) { this.db = db; this.sql = sql; this.args = []; }
  bind(...args) { this.args = args; return this; }
  async first() { const row = this.db.prepare(this.sql).get(...this.args); return row ? { ...row } : null; }
  async all() { return { results: this.db.prepare(this.sql).all(...this.args).map((r) => ({ ...r })) }; }
  async run() { return { meta: { changes: Number(this.db.prepare(this.sql).run(...this.args).changes) } }; }
}

class FakeD1 {
  constructor() { this.db = new DatabaseSync(":memory:"); this.db.exec(SCHEMA); }
  prepare(sql) { return new Statement(this.db, sql); }
  async batch(statements) { for (const s of statements) await s.run(); }
  rows(sql, ...args) { return this.db.prepare(sql).all(...args).map((r) => ({ ...r })); }
}

const MENTIONS = (mentions) => JSON.stringify({ format: 1, site: "Salem Publick", days: 30, mentions });

function mention(posted, addresses, extra = {}) {
  return {
    addresses, names: addresses.map((a) => a.replace(/ ST$/, " Street").replace(/^(\d+) (\w)(\w*)/, (_, n, a1, rest) => `${n} ${a1}${rest.toLowerCase()}`)),
    streets: [], board: "Planning Board", kind: "agenda", date: posted.slice(0, 10), url: "/meetings/2026-11-05-planning-board/",
    posted, address: addresses[0], line: `A site plan at ${addresses[0]}`, ...extra,
  };
}

let env, sent, mentionsJson;

function manifest() {
  return {
    files: {
      "alerts/thanks/index.html": { blob: "thanks" },
      "streets/feeds/main-st.xml": { blob: "feed" },
      "streets/feeds/high-st.xml": { blob: "feed2" },
      "streets/mentions.json": { blob: "mentions" },
    },
  };
}

function postmark(answer = (messages) => messages.map(() => ({ ErrorCode: 0 }))) {
  return async (url, init) => {
    const messages = JSON.parse(init.body);
    sent.push(...messages);
    const result = answer(messages);
    return result instanceof Response ? result : new Response(JSON.stringify(result), { status: 200 });
  };
}

beforeEach(() => {
  sent = [];
  mentionsJson = MENTIONS([]);
  env = {
    ALERTS_DB: new FakeD1(), POSTMARK_TOKEN: "pm", ALERTS_FROM: "Publick alerts <alerts@alerts.example.org>",
    ALERTS_HOOK_SECRET: HOOK, ALERTS_TOKEN_SECRET: "token-secret", ALERTS_MAILING_ADDRESS: "PO Box 1, Salem, MA",
    SITES: { get: async (key) => (key === "blobs/mentions" ? { text: async () => mentionsJson } : null) },
  };
  clearManifests();
});

function form(path, fields, headers = {}) {
  return new Request(`${ORIGIN}${path}`, {
    method: "POST", body: new URLSearchParams(fields).toString(),
    headers: { Origin: ORIGIN, "Content-Type": "application/x-www-form-urlencoded", ...headers },
  });
}

const followForm = (fields = {}) => form("/alerts/follow", { email: "Reader@Example.org", number: "62", street: "MAIN ST", lang: "en", ...fields });

async function followAndConfirm(email, number = "62", street = "MAIN ST") {
  await follow(followForm({ email, number, street }), env, new URL(`${ORIGIN}/alerts/follow`), manifest(), new Date(), postmark());
  const token = new URL(sent.at(-1).TextBody.match(/https:\S+confirm\S+/)[0]).searchParams.get("t");
  const answer = await confirm(form("/alerts/confirm", { t: token }), env, new URL(`${ORIGIN}/alerts/confirm`));
  assert.equal(answer.status, 200);
}

test("a follow is kept unconfirmed and the address is asked to confirm", async () => {
  const answer = await follow(followForm(), env, new URL(`${ORIGIN}/alerts/follow`), manifest(), new Date(), postmark());
  assert.equal(answer.status, 303);
  assert.equal(answer.headers.get("Location"), `${ORIGIN}/alerts/thanks/`);
  const [row] = env.ALERTS_DB.rows("SELECT * FROM follows");
  assert.deepEqual([row.email, row.town, row.kind, row.key, row.confirmed_at], ["reader@example.org", "salem-ma", "address", "62 MAIN ST", null]);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].To, "reader@example.org");
  assert.equal(sent[0].Subject, "Confirm: follow 62 Main St");
  assert.equal(sent[0].TrackOpens, false);
  assert.equal(sent[0].TrackLinks, "None");
  // The link's token is stored only as its hash.
  const token = new URL(sent[0].TextBody.match(/https:\S+confirm\S+/)[0]).searchParams.get("t");
  assert.equal(row.token, await hashed(token));
  assert.ok(!JSON.stringify(env.ALERTS_DB.rows("SELECT * FROM follows")).includes(token));
});

test("a follow that can't be taken gets the same answers and sends nothing", async () => {
  const url = new URL(`${ORIGIN}/alerts/follow`);
  const problem = async (request) => (await follow(request, env, url, manifest(), new Date(), postmark())).headers.get("Location");
  // Another site's page, a street the documents don't name, a bad number, a bad address.
  assert.equal(await problem(form("/alerts/follow", { email: "a@b.org", number: "1", street: "MAIN ST" }, { Origin: "https://evil.example" })), `${ORIGIN}/alerts/problem/`);
  assert.equal(await problem(followForm({ street: "ELM ST" })), `${ORIGIN}/alerts/problem/`);
  assert.equal(await problem(followForm({ number: "12; DROP" })), `${ORIGIN}/alerts/problem/`);
  assert.equal(await problem(followForm({ email: "not an address" })), `${ORIGIN}/alerts/problem/`);
  // The hidden field bots fill in: answered as done, and nothing kept or sent.
  assert.equal(await problem(followForm({ website: "spam" })), `${ORIGIN}/alerts/thanks/`);
  assert.equal(sent.length, 0);
  assert.equal(env.ALERTS_DB.rows("SELECT * FROM follows").length, 0);
});

test("an address follows at most MAX_FOLLOWS, and a confirmed follow isn't asked again", async () => {
  const url = new URL(`${ORIGIN}/alerts/follow`);
  // A day apart, so the day's limit on confirmation emails doesn't stop them.
  for (let n = 1; n <= MAX_FOLLOWS; n++) {
    await follow(followForm({ number: String(n) }), env, url, manifest(), new Date(Date.UTC(2026, 9, n)), postmark());
  }
  const over = await follow(followForm({ number: "999" }), env, url, manifest(), new Date(), postmark());
  assert.equal(over.headers.get("Location"), `${ORIGIN}/alerts/problem/`);
  assert.equal(env.ALERTS_DB.rows("SELECT * FROM follows").length, MAX_FOLLOWS);
  env.ALERTS_DB = new FakeD1();
  sent = [];
  await followAndConfirm("reader@example.org");
  await follow(followForm(), env, url, manifest(), new Date(), postmark());
  assert.equal(sent.length, 1);
});

test("an address gets at most CONFIRMS_PER_DAY confirmation emails a day", async () => {
  const url = new URL(`${ORIGIN}/alerts/follow`);
  const now = new Date("2026-11-07T10:00:00Z");
  for (let n = 0; n < CONFIRMS_PER_DAY + 3; n++) {
    const answer = await follow(followForm({ number: String(n + 1) }), env, url, manifest(), now, postmark());
    assert.equal(answer.headers.get("Location"), `${ORIGIN}/alerts/thanks/`);
  }
  assert.equal(sent.length, CONFIRMS_PER_DAY);
  await follow(followForm({ number: "99" }), env, url, manifest(), new Date("2026-11-08T11:00:00Z"), postmark());
  assert.equal(sent.length, CONFIRMS_PER_DAY + 1);
});

test("confirming takes the button's post, and an unknown token confirms nothing", async () => {
  await followAndConfirm("reader@example.org");
  assert.ok(env.ALERTS_DB.rows("SELECT confirmed_at FROM follows")[0].confirmed_at);
  const unknown = await confirm(form("/alerts/confirm", { t: "nope" }), env, new URL(`${ORIGIN}/alerts/confirm`));
  assert.equal(unknown.status, 404);
});

function hook(secret = HOOK) {
  return new Request(`${ORIGIN}/alerts/send`, { method: "POST", headers: { Authorization: `Bearer ${secret}` } });
}

test("a send emails each reader once, with what they follow, and not again that day", async () => {
  await followAndConfirm("one@example.org", "62");
  await followAndConfirm("one@example.org", "7", "HIGH ST");
  await followAndConfirm("two@example.org", "62");
  await follow(followForm({ email: "unconfirmed@example.org" }), env, new URL(`${ORIGIN}/alerts/follow`), manifest(), new Date(), postmark());
  sent = [];
  const url = new URL(`${ORIGIN}/alerts/send`);
  assert.equal((await send(hook("wrong"), env, url, manifest(), new Date("2026-11-06T10:00:00Z"), postmark())).status, 403);
  // The first send starts from now: mentions already there aren't news.
  mentionsJson = MENTIONS([mention("2026-11-05T09:00:00-05:00", ["62 MAIN ST"])]);
  let answer = await (await send(hook(), env, url, manifest(), new Date("2026-11-06T10:00:00Z"), postmark())).json();
  assert.deepEqual([answer.sent, sent.length], [0, 0]);
  mentionsJson = MENTIONS([
    mention("2026-11-07T04:00:00-05:00", ["62 MAIN ST", "64 MAIN ST"]),
    mention("2026-11-07T04:00:00-05:00", ["7 HIGH ST"], { kind: "minutes", board: "Zoning Board" }),
    mention("2026-11-05T09:00:00-05:00", ["62 MAIN ST"]),
  ]);
  answer = await (await send(hook(), env, url, manifest(), new Date("2026-11-07T10:00:00Z"), postmark())).json();
  assert.deepEqual([answer.sent, answer.mentions], [2, 2]);
  const byReader = Object.fromEntries(sent.map((m) => [m.To, m]));
  assert.deepEqual(Object.keys(byReader).sort(), ["one@example.org", "two@example.org"]);
  const one = byReader["one@example.org"];
  assert.equal(one.Subject, "2 addresses you follow came up");
  assert.match(one.TextBody, /62 Main Street\nPlanning Board, agenda for November 7, 2026\n“A site plan at 62 MAIN ST”\nhttps:\/\/salem-ma\.publick\.org\/meetings\/2026-11-05-planning-board\/\?ref=alert-email/);
  assert.match(one.TextBody, /7 High Street\nZoning Board, minutes of November 7, 2026/);
  assert.match(one.TextBody, /PO Box 1, Salem, MA/);
  assert.equal(byReader["two@example.org"].Subject, "62 Main Street: Planning Board");
  const unsubscribeHeader = one.Headers.find((h) => h.Name === "List-Unsubscribe").Value;
  assert.match(unsubscribeHeader, /^<https:\/\/salem-ma\.publick\.org\/alerts\/unsubscribe\?m=/);
  assert.ok(one.Headers.some((h) => h.Name === "List-Unsubscribe-Post" && h.Value === "List-Unsubscribe=One-Click"));
  assert.ok(one.TextBody.includes("/alerts/manage/?m="));
  // A second publish the same day waits for the next.
  sent = [];
  mentionsJson = MENTIONS([mention("2026-11-07T13:00:00-05:00", ["62 MAIN ST"])]);
  answer = await (await send(hook(), env, url, manifest(), new Date("2026-11-07T18:00:00Z"), postmark())).json();
  assert.equal(answer.skipped, "sent today");
  answer = await (await send(hook(), env, url, manifest(), new Date("2026-11-08T10:00:00Z"), postmark())).json();
  assert.equal(answer.sent, 2);
});

test("an email the provider refuses is tried on the next publish, and one it took isn't sent twice", async () => {
  await followAndConfirm("one@example.org");
  await followAndConfirm("two@example.org");
  const url = new URL(`${ORIGIN}/alerts/send`);
  await send(hook(), env, url, manifest(), new Date("2026-11-06T10:00:00Z"), postmark());
  mentionsJson = MENTIONS([mention("2026-11-07T04:00:00-05:00", ["62 MAIN ST"])]);
  sent = [];
  const refuseTwo = postmark((messages) => messages.map((m) => ({ ErrorCode: m.To === "two@example.org" ? 406 : 0 })));
  const first = await send(hook(), env, url, manifest(), new Date("2026-11-07T10:00:00Z"), refuseTwo);
  assert.equal(first.status, 502);
  sent = [];
  const again = await (await send(hook(), env, url, manifest(), new Date("2026-11-07T11:00:00Z"), postmark())).json();
  assert.deepEqual(sent.map((m) => m.To), ["two@example.org"]);
  assert.equal(again.sent, 1);
});

test("a reader's links are the same in every email, list their follows, and remove them", async () => {
  await followAndConfirm("one@example.org", "62");
  await followAndConfirm("one@example.org", "7", "HIGH ST");
  const first = await readerToken(env, "one@example.org");
  assert.equal(await readerToken(env, "one@example.org"), first);
  const url = new URL(`${ORIGIN}/alerts/manage`);
  let page = await (await manage(form("/alerts/manage", { m: first }), env, url)).json();
  assert.deepEqual(page.follows, [{ town: "salem-ma", key: "62 MAIN ST", name: "62 Main St" }, { town: "salem-ma", key: "7 HIGH ST", name: "7 High St" }]);
  page = await (await manage(form("/alerts/manage", { m: first, remove: "salem-ma|7 HIGH ST" }), env, url)).json();
  assert.deepEqual(page.follows, [{ town: "salem-ma", key: "62 MAIN ST", name: "62 Main St" }]);
  assert.equal((await manage(form("/alerts/manage", { m: "wrong" }), env, url)).status, 404);
  // One-click unsubscribe: a mail app's post, with no Origin, removes everything about the reader.
  const oneClick = new Request(`${ORIGIN}/alerts/unsubscribe?m=${first}`, {
    method: "POST", body: "List-Unsubscribe=One-Click", headers: { "Content-Type": "application/x-www-form-urlencoded" },
  });
  assert.equal((await unsubscribe(oneClick, env, new URL(oneClick.url))).status, 200);
  assert.deepEqual(env.ALERTS_DB.rows("SELECT * FROM follows"), []);
  assert.deepEqual(env.ALERTS_DB.rows("SELECT * FROM readers"), []);
});

test("an unconfirmed follow is deleted after UNCONFIRMED_DAYS", async () => {
  await follow(followForm(), env, new URL(`${ORIGIN}/alerts/follow`), manifest(), new Date("2026-11-01T10:00:00Z"), postmark());
  const later = new Date(Date.parse("2026-11-01T10:00:00Z") + (UNCONFIRMED_DAYS + 1) * 86_400_000);
  await send(hook(), env, new URL(`${ORIGIN}/alerts/send`), manifest(), later, postmark());
  assert.deepEqual(env.ALERTS_DB.rows("SELECT * FROM follows"), []);
});

test("street keys as names and folders", () => {
  assert.equal(streetLabel("EASTERN POINT BLVD"), "Eastern Point Blvd");
  assert.equal(streetLabel("1ST ST"), "1st St");
  assert.equal(slug("EASTERN POINT BLVD"), "eastern-point-blvd");
});

test("a site without alerts doesn't take their posts", async () => {
  const objects = {
    "sites/salem-ma.publick.org/current.json": JSON.stringify({ format: 1, files: { "index.html": { blob: "home", type: "text/html", size: 4 } } }),
  };
  const sitesEnv = { ...env, SITES: { get: async (key) => (objects[key] ? { text: async () => objects[key], json: async () => JSON.parse(objects[key]), body: objects[key] } : null) } };
  const answer = await worker.fetch(followForm(), sitesEnv, {});
  assert.equal(answer.status, 405);
  assert.equal(env.ALERTS_DB.rows("SELECT * FROM follows").length, 0);
});
