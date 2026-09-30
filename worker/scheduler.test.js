// Run with: node --test worker/scheduler.test.js
import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import worker from "./scheduler-index.js";
import { ALERT_LABEL, STALE_HOURS, lastDailyRun, onSchedule, startRun, watch } from "./scheduler.js";

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
