// Run with: node --test worker/scheduler.test.js
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import worker from "./scheduler-index.js";
import { ALERT_LABEL, DIGEST_LABEL, ENGINE_CRON, RELEASE_CRON, STALE_HOURS, lastDailyRun, onSchedule, startRun, watch } from "./scheduler.js";
import { DIGEST_CRON, newestIssue, sendDigests } from "./digest.js";

const NOW = new Date("2026-10-02T13:05:00Z");

function statusPage(lastRun) {
  const meta = lastRun ? `<meta name="last-daily-run" content="${lastRun}">` : "";
  return `<!doctype html><html><head>${meta}<title>Network status</title></head><body></body></html>`;
}

function env(page = statusPage("2026-10-02T11:40:00+00:00"), status = 200) {
  return {
    GITHUB_TOKEN: "token",
    REPOSITORY: "publick-org/publick.org",
    WORKFLOW: "network.yml",
    STATUS_URL: "https://publick.org/status/",
    ALERT_ASSIGNEE: "maintainer",
    SITES: { fetch: async (url) => (url === "https://publick.org/status/" ? new Response(page, { status }) : new Response("", { status: 404 })) },
  };
}

// GitHub's API, recorded: each call's method, path, and body; answers from `routes` ("METHOD path" -> [status, body]).
let calls;
let routes;
const realFetch = globalThis.fetch;

beforeEach(() => {
  calls = [];
  routes = {};
  globalThis.fetch = async (url, init = {}) => {
    const path = url.replace("https://api.github.com", "");
    const method = init.method || "GET";
    calls.push({ method, path, body: init.body ? JSON.parse(init.body) : null, headers: init.headers });
    const [status, body] = routes[`${method} ${path.split("?")[0]}`] || [200, []];
    return new Response(status === 204 ? null : JSON.stringify(body), { status });
  };
});

afterEach(() => {
  globalThis.fetch = realFetch;
});

test("starts a daily run of the network workflow", async () => {
  routes["POST /repos/publick-org/publick.org/actions/workflows/network.yml/dispatches"] = [204, null];
  await startRun(env());
  assert.equal(calls.length, 1);
  assert.deepEqual(calls[0].body, { ref: "main", inputs: { daily: "true" } });
  assert.equal(calls[0].headers.Authorization, "Bearer token");
  assert.equal(calls[0].headers["User-Agent"], "publick-scheduler");
});

test("a refused start is an error, naming the status", async () => {
  routes["POST /repos/publick-org/publick.org/actions/workflows/network.yml/dispatches"] = [401, { message: "Bad credentials" }];
  await assert.rejects(startRun(env()), /network\.yml: HTTP 401 .*Bad credentials/);
});

test("reads the last daily run from the status page", async () => {
  assert.equal((await lastDailyRun(env())).toISOString(), "2026-10-02T11:40:00.000Z");
  assert.equal(await lastDailyRun(env(statusPage(null))), null);
  assert.equal(await lastDailyRun(env(statusPage("not a time"))), null);
  assert.equal(await lastDailyRun(env("", 500)), null);
});

test("recent runs: nothing to do", async () => {
  const result = await watch(env(), NOW);
  assert.equal(result.stopped, false);
  assert.deepEqual(calls.map((c) => c.method), ["GET"]);
});

test("no daily run for a day and more opens one issue, assigned", async () => {
  const old = new Date(NOW - (STALE_HOURS + 1) * 3600_000).toISOString();
  routes["POST /repos/publick-org/publick.org/labels"] = [422, { message: "already_exists" }];
  routes["POST /repos/publick-org/publick.org/issues"] = [201, { number: 7 }];
  const result = await watch(env(statusPage(old)), NOW);
  assert.equal(result.stopped, true);
  const issue = calls.find((c) => c.method === "POST" && c.path === "/repos/publick-org/publick.org/issues");
  assert.deepEqual(issue.body.labels, [ALERT_LABEL]);
  assert.deepEqual(issue.body.assignees, ["maintainer"]);
  assert.match(issue.body.body, /more than 30 hours/);
});

test("an issue already open isn't opened again", async () => {
  routes["GET /repos/publick-org/publick.org/issues"] = [200, [{ number: 7 }]];
  await watch(env(statusPage(null)), NOW);
  assert.ok(!calls.some((c) => c.method === "POST"));
});

test("runs finishing again close the issue", async () => {
  routes["GET /repos/publick-org/publick.org/issues"] = [200, [{ number: 7 }]];
  routes["POST /repos/publick-org/publick.org/issues/7/comments"] = [201, {}];
  routes["PATCH /repos/publick-org/publick.org/issues/7"] = [200, {}];
  await watch(env(), NOW);
  const close = calls.find((c) => c.method === "PATCH");
  assert.deepEqual(close.body, { state: "closed", state_reason: "completed" });
});

test("a failed start still checks on the runs, and fails the invocation", async () => {
  routes["POST /repos/publick-org/publick.org/actions/workflows/network.yml/dispatches"] = [500, { message: "oops" }];
  await assert.rejects(onSchedule({ scheduledTime: NOW.getTime() }, env()), /HTTP 500/);
  assert.ok(calls.some((c) => c.method === "GET" && c.path.startsWith("/repos/publick-org/publick.org/issues")));
});

test("the Worker's scheduled handler starts a run", async () => {
  routes["POST /repos/publick-org/publick.org/actions/workflows/network.yml/dispatches"] = [204, null];
  await worker.scheduled({ scheduledTime: NOW.getTime(), cron: "5 9-14 * * *" }, env());
  assert.ok(calls.some((c) => c.path.endsWith("/dispatches")));
});

test("08:20 starts the engine's release, and nothing else", async () => {
  routes["POST /repos/publick-org/publick-engine/actions/workflows/release.yml/dispatches"] = [204, null];
  await onSchedule({ scheduledTime: NOW.getTime(), cron: RELEASE_CRON }, env());
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`),
    ["POST /repos/publick-org/publick-engine/actions/workflows/release.yml/dispatches"]);
  assert.deepEqual(calls[0].body, { ref: "main" });
});

test("08:40 starts the network's engine pull request, and nothing else", async () => {
  routes["POST /repos/publick-org/publick.org/actions/workflows/engine.yml/dispatches"] = [204, null];
  await worker.scheduled({ scheduledTime: NOW.getTime(), cron: ENGINE_CRON }, env());
  assert.deepEqual(calls.map((c) => `${c.method} ${c.path}`),
    ["POST /repos/publick-org/publick.org/actions/workflows/engine.yml/dispatches"]);
});

test("a refused release start fails the invocation, naming the repository", async () => {
  routes["POST /repos/publick-org/publick-engine/actions/workflows/release.yml/dispatches"] = [403, { message: "Resource not accessible" }];
  await assert.rejects(onSchedule({ scheduledTime: NOW.getTime(), cron: RELEASE_CRON }, env()),
    /publick-org\/publick-engine's release\.yml: HTTP 403/);
});

// ---- The weekly digest's send (digest.js) ----

// A town's /digest/feed.xml, as pipeline/build_site.py write_digest_feed writes it.
function digestFeed(site, title, link, pubDate, body) {
  const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return '<?xml version="1.0" encoding="UTF-8"?>\n<rss version="2.0"><channel>\n' +
    `<title>${esc(site)}: Weekly digest</title><link>${link.replace(/\d{4}-\d{2}-\d{2}\/$/, "")}</link><description>Each week</description>` +
    `<lastBuildDate>Sun, 04 Oct 2026 07:00:00 -0400</lastBuildDate>\n` +
    `<item><title>${esc(title)}</title><link>${link}</link><guid isPermaLink="true">${link}</guid>` +
    `<pubDate>${pubDate}</pubDate><description>${esc(body)}</description></item>\n` +
    `<item><title>Week of September 28, 2026</title><link>https://x/digest/2026-09-28/</link><guid isPermaLink="true">x</guid>` +
    "<pubDate>Sun, 27 Sep 2026 17:30:00 -0400</pubDate><description>old</description></item>\n</channel></rss>\n";
}

const DUE = "Sun, 04 Oct 2026 17:30:00 -0400";  // 21:30 UTC
const BODY = '<p>13 meetings this week.</p>\n<h2>Meetings</h2><p><a href="https://gloucester-ma.publick.org/meetings/x/?ref=digest">Board &amp; Committee</a></p>';

function digestEnv(towns = "gloucester-ma.publick.org", feeds = null) {
  feeds ??= {
    "https://gloucester-ma.publick.org/digest/feed.xml": [200, digestFeed("Gloucester Publick", "Week of October 5, 2026",
      "https://gloucester-ma.publick.org/digest/2026-10-05/", DUE, BODY)],
    "https://malden-ma.publick.org/digest/feed.xml": [200, digestFeed("Malden Publick", "Week of October 5, 2026",
      "https://malden-ma.publick.org/digest/2026-10-05/", DUE, "<p>Malden</p>")],
  };
  return { ...env(), BUTTONDOWN_SEND_KEY: "send-key", DIGEST_TOWNS: towns,
           SITES: { fetch: async (url) => { const [status, body] = feeds[url] || [404, ""]; return new Response(body, { status }); } } };
}

// Buttondown's API (and GitHub's), recorded, with Buttondown's answers by "METHOD path-without-query".
let buttondownRoutes;

function withButtondown(routesFor) {
  buttondownRoutes = routesFor;
  const github = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    if (!url.startsWith("https://api.buttondown.com/v1")) return github(url, init);
    const path = url.replace("https://api.buttondown.com/v1", "");
    const method = init.method || "GET";
    calls.push({ method, path, body: init.body ? JSON.parse(init.body) : null, headers: init.headers });
    const [status, body] = buttondownRoutes[`${method} ${path.split("?")[0]}`] || [500, { detail: "not expected" }];
    return new Response(JSON.stringify(body), { status });
  };
}

const TAGS = { results: [{ id: "tag_gloucester", name: "gloucester-ma-en" }, { id: "tag_malden", name: "malden-ma-en" }], next: null };
const NONE_SENT = { results: [], next: null };
const at = (iso) => new Date(iso);

test("an issue due is sent once, to its town's readers only", async () => {
  withButtondown({ "GET /emails": [200, NONE_SENT], "GET /tags": [200, TAGS], "POST /emails": [201, { id: "em_1" }] });
  const results = await sendDigests(digestEnv(), at("2026-10-04T21:35:00Z"));
  assert.deepEqual(results, [{ host: "gloucester-ma.publick.org", done: "sent", subject: "Gloucester Publick: Week of October 5, 2026" }]);
  const sent = calls.find((c) => c.method === "POST" && c.path === "/emails");
  assert.equal(sent.headers.Authorization, "Token send-key");
  assert.equal(sent.headers["X-Buttondown-Live-Dangerously"], "true");
  assert.equal(sent.body.subject, "Gloucester Publick: Week of October 5, 2026");
  assert.equal(sent.body.body, `<!-- buttondown-editor-mode: fancy -->\n${BODY}`);
  assert.equal(sent.body.status, "about_to_send");
  assert.deepEqual(sent.body.filters, { predicate: "and", groups: [],
    filters: [{ field: "subscriber.tags", operator: "contains", value: "tag_gloucester" }] });
  assert.equal(sent.body.slug, "gloucester-ma-2026-10-05");
  assert.equal(sent.body.archival_mode, "disabled");
  assert.equal(sent.body.canonical_url, "https://gloucester-ma.publick.org/digest/2026-10-05/");
});

test("an issue isn't sent before it's due, nor more than 6 hours late", async () => {
  withButtondown({});
  for (const now of ["2026-10-04T21:29:00Z", "2026-10-05T03:31:00Z"]) {
    const [result] = await sendDigests(digestEnv(), at(now));
    assert.equal(result.done, "not_due", now);
  }
  assert.equal(calls.length, 0);
});

test("an issue already sent isn't sent again", async () => {
  withButtondown({ "GET /emails": [200, { results: [{ subject: "Gloucester Publick: Week of October 5, 2026", status: "sent" }], next: null }] });
  const [result] = await sendDigests(digestEnv(), at("2026-10-05T00:35:00Z"));
  assert.equal(result.done, "already_sent");
  assert.ok(!calls.some((c) => c.method === "POST"));
});

test("a town no one has signed up for yet sends nothing", async () => {
  withButtondown({ "GET /emails": [200, NONE_SENT], "GET /tags": [200, { results: [], next: null }] });
  const [result] = await sendDigests(digestEnv(), at("2026-10-04T21:35:00Z"));
  assert.equal(result.done, "no_subscribers");
  assert.ok(!calls.some((c) => c.method === "POST"));
});

test("no towns named, nothing is read or sent", async () => {
  withButtondown({});
  assert.deepEqual(await sendDigests(digestEnv(""), at("2026-10-04T21:35:00Z")), []);
  assert.equal(calls.length, 0);
});

test("one town failing doesn't stop the next; the run fails naming it, and opens one issue", async () => {
  const feeds = { "https://gloucester-ma.publick.org/digest/feed.xml": [503, ""],
                  "https://malden-ma.publick.org/digest/feed.xml": [200, digestFeed("Malden Publick", "Week of October 5, 2026",
                    "https://malden-ma.publick.org/digest/2026-10-05/", DUE, "<p>Malden</p>")] };
  withButtondown({ "GET /emails": [200, NONE_SENT], "GET /tags": [200, TAGS], "POST /emails": [201, {}] });
  routes["GET /repos/publick-org/publick.org/issues"] = [200, []];
  routes["POST /repos/publick-org/publick.org/labels"] = [201, {}];
  routes["POST /repos/publick-org/publick.org/issues"] = [201, { number: 9 }];
  const controller = { cron: DIGEST_CRON, scheduledTime: at("2026-10-04T21:35:00Z").getTime() };
  await assert.rejects(onSchedule(controller, digestEnv("gloucester-ma.publick.org, malden-ma.publick.org", feeds)),
    /gloucester-ma\.publick\.org: its feed: HTTP 503/);
  const sent = calls.filter((c) => c.method === "POST" && c.path === "/emails");
  assert.equal(sent.length, 1);
  assert.equal(sent[0].body.filters.filters[0].value, "tag_malden");
  const issue = calls.find((c) => c.method === "POST" && c.path === "/repos/publick-org/publick.org/issues");
  assert.equal(issue.body.title, "The weekly digest didn't send");
  assert.deepEqual(issue.body.labels, [DIGEST_LABEL]);
  assert.match(issue.body.body, /gloucester-ma\.publick\.org: its feed: HTTP 503/);
  // Not a daily run.
  assert.ok(!calls.some((c) => c.path.includes("/dispatches")));
});

test("the newest issue in a feed, with the site's name and its body unescaped", () => {
  const issue = newestIssue(digestFeed("Gloucester Publick", "Week of October 5, 2026",
    "https://gloucester-ma.publick.org/digest/2026-10-05/", DUE, BODY));
  assert.equal(issue.subject, "Gloucester Publick: Week of October 5, 2026");
  assert.equal(issue.due.toISOString(), "2026-10-04T21:30:00.000Z");
  assert.equal(issue.body, BODY);
  assert.equal(newestIssue('<rss version="2.0"><channel>\n<title>X: Weekly digest</title></channel></rss>'), null);
});

test("the digest's Cron Trigger names its days, as Cloudflare takes them", () => {
  // Cloudflare numbers days 1 (Sunday) to 7 and refused "0,1" (2026-10-06); names mean the same everywhere.
  const days = DIGEST_CRON.split(" ")[4];
  assert.match(days, /^(SUN|MON|TUE|WED|THU|FRI|SAT)(,(SUN|MON|TUE|WED|THU|FRI|SAT))*$/);
  assert.equal(DIGEST_CRON.split(" ").length, 5);
});
