// The network's scheduler (scheduler-index.js, a Worker of its own, apart from
// the one that serves the sites). Through GitHub's API, as "Run workflow" does,
// it starts the morning's work on time: at RELEASE_CRON the engine's release,
// at ENGINE_CRON the network's engine pull request (which moves engine-version
// to that release), and on its other Cron Triggers the network's daily run,
// checking that daily runs are still finishing.
//
// GitHub starts scheduled workflows when it can, sometimes hours late, and can
// drop one; a Cron Trigger fires on time. (On 2026-10-01 and 02 every scheduled
// run of the network's started 5 to 7 hours late, and the first morning's
// release and engine pull request, on GitHub's schedule, hadn't started by noon.)
// The workflows keep their own schedules as a late backup: a release with
// nothing new does nothing, and an engine pull request with no newer release too. A daily run takes only the towns that
// are due (pipeline.network plan --due-hours), so starting one every hour of
// the morning does no harm: a start with nothing due does nothing.
//
// If no town's daily run has finished for STALE_HOURS (the status page's
// last-daily-run), it opens an issue, assigned to the maintainer, so a network
// that has stopped altogether still reaches someone; it closes the issue when
// runs finish again.
//
// On DIGEST_CRON it sends the towns' weekly digests (digest.js), and if any
// fails, opens an issue (or comments on the one open) saying which.
//
// On UPTIME_CRON, every hour, it loads each site's homepage through the sites
// Worker: publick.org and every town in the network's sitemap (SITEMAP_URL,
// publick.org/sitemap.xml, which lists each live town from its first build).
// A site that doesn't load, tried twice a few seconds apart, opens a "site
// down" issue listing it; the issue is kept up to date while any is down and
// closed when all load again. The daily runs check each site they publish, but
// a site that stops loading between them (a release the sites Worker can't
// read, the bucket) would otherwise wait for the next morning to be noticed.
// It doesn't see what's in front of the sites Worker (DNS, its routes).
//
// Environment (the network repository's wrangler.scheduler.toml): GITHUB_TOKEN,
// a secret (a fine-grained token for the network repository with Actions and
// Issues read and write, and for the engine repository with Actions read and
// write); REPOSITORY, WORKFLOW, BRANCH, STATUS_URL, and ALERT_ASSIGNEE;
// ENGINE_REPOSITORY, RELEASE_WORKFLOW, and ENGINE_WORKFLOW; and SITES, a
// service binding to the Worker that serves the sites, which the status page
// is read through; and for the digest, BUTTONDOWN_SEND_KEY and DIGEST_TOWNS (digest.js).

import { DIGEST_CRON, sendDigests } from "./digest.js";

export const STALE_HOURS = 30;
// The Cron Triggers (as wrangler.scheduler.toml writes them) that start the release and the engine
// pull request; DIGEST_CRON (digest.js) sends the weekly digest; every other one starts a daily run.
export const RELEASE_CRON = "20 8 * * *";
export const ENGINE_CRON = "40 8 * * *";
export const UPTIME_CRON = "50 * * * *";
export const ALERT_LABEL = "network stopped";
export const DOWN_LABEL = "site down";
// How long the uptime check waits before trying a site that didn't load once more.
export const RETRY_MS = 5000;
export const DIGEST_LABEL = "digest not sent";
const API = "https://api.github.com";

function github(env, path, init = {}) {
  return fetch(`${API}${path}`, {
    ...init,
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "publick-scheduler",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

async function ok(response, what) {
  if (!response.ok) throw new Error(`${what}: HTTP ${response.status} ${await response.text()}`);
  return response;
}

// Starts a daily run of the network workflow: the towns that are due.
export async function startRun(env) {
  await ok(await github(env, `/repos/${env.REPOSITORY}/actions/workflows/${env.WORKFLOW}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: env.BRANCH || "main", inputs: { daily: "true" } }),
  }), `starting ${env.WORKFLOW}`);
}

// Starts a workflow with no inputs: the engine's release, or the network's engine pull request.
export async function startWorkflow(env, repository, workflow) {
  await ok(await github(env, `/repos/${repository}/actions/workflows/${workflow}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: env.BRANCH || "main" }),
  }), `starting ${repository}'s ${workflow}`);
}

// When the last town's daily run finished, from the status page, or null if it doesn't say.
export async function lastDailyRun(env) {
  const response = await env.SITES.fetch(env.STATUS_URL);
  if (!response.ok) return null;
  const found = (await response.text()).match(/<meta name="last-daily-run" content="([^"]+)"/);
  const at = found ? new Date(found[1]) : null;
  return at && !Number.isNaN(at.getTime()) ? at : null;
}

function alertBody(last) {
  const since = last ? `since ${last.toISOString().slice(0, 16).replace("T", " ")} UTC` : "for as long as the status page shows";
  return `No town's daily run has finished ${since}, more than ${STALE_HOURS} hours. ` +
    "The runs may not be starting, or not finishing: see the Network workflow's runs on GitHub Actions, " +
    "and the publick-scheduler Worker's logs on Cloudflare (its GitHub token may have expired). " +
    "The sites still work, with the data they had.\n\n" +
    "The scheduler checks every hour of the morning and closes this issue when a daily run finishes.";
}

// Opens the "network stopped" issue when no daily run has finished for STALE_HOURS, and closes it once one has.
export async function watch(env, now = new Date()) {
  const last = await lastDailyRun(env);
  const stopped = !last || now - last > STALE_HOURS * 3600_000;
  const label = encodeURIComponent(ALERT_LABEL);
  const open = await (await ok(await github(env, `/repos/${env.REPOSITORY}/issues?state=open&labels=${label}`),
    "listing issues")).json();
  if (stopped && open.length === 0) {
    // The label is made if it isn't there yet; 422 means it already is.
    const made = await github(env, `/repos/${env.REPOSITORY}/labels`, {
      method: "POST",
      body: JSON.stringify({ name: ALERT_LABEL, color: "B60205", description: "The network's daily runs have stopped" }),
    });
    if (!made.ok && made.status !== 422) await ok(made, "making the label");
    await ok(await github(env, `/repos/${env.REPOSITORY}/issues`, {
      method: "POST",
      body: JSON.stringify({
        title: "The network's daily runs have stopped",
        body: alertBody(last),
        labels: [ALERT_LABEL],
        ...(env.ALERT_ASSIGNEE ? { assignees: [env.ALERT_ASSIGNEE] } : {}),
      }),
    }), "opening the issue");
  } else if (!stopped) {
    for (const issue of open) {
      await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${issue.number}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: `A daily run finished at ${last.toISOString().slice(0, 16).replace("T", " ")} UTC.` }),
      }), "commenting");
      await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${issue.number}`, {
        method: "PATCH",
        body: JSON.stringify({ state: "closed", state_reason: "completed" }),
      }), "closing the issue");
    }
  }
  return { stopped, last };
}

// Opens the "digest not sent" issue for a digest run that failed, or comments on the one already open.
export async function reportDigestFailure(env, error, now = new Date()) {
  const label = encodeURIComponent(DIGEST_LABEL);
  const open = await (await ok(await github(env, `/repos/${env.REPOSITORY}/issues?state=open&labels=${label}`),
    "listing issues")).json();
  const towns = (error.errors || [error]).map((e) => `- ${e.message}`).join("\n");
  const body = `The weekly digest's run at ${now.toISOString().slice(0, 16).replace("T", " ")} UTC failed:\n\n${towns}\n\n` +
    "Each failed town's issue is tried again every hour until it's 6 hours past due; after that it isn't sent. " +
    "See the publick-scheduler Worker's logs on Cloudflare, and Buttondown's API log. Close this issue once it's fixed.";
  if (open.length) {
    await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${open[0].number}/comments`, {
      method: "POST", body: JSON.stringify({ body }),
    }), "commenting");
    return;
  }
  const made = await github(env, `/repos/${env.REPOSITORY}/labels`, {
    method: "POST",
    body: JSON.stringify({ name: DIGEST_LABEL, color: "D93F0B", description: "A weekly digest email didn't send" }),
  });
  if (!made.ok && made.status !== 422) await ok(made, "making the label");
  await ok(await github(env, `/repos/${env.REPOSITORY}/issues`, {
    method: "POST",
    body: JSON.stringify({
      title: "The weekly digest didn't send", body, labels: [DIGEST_LABEL],
      ...(env.ALERT_ASSIGNEE ? { assignees: [env.ALERT_ASSIGNEE] } : {}),
    }),
  }), "opening the issue");
}

// The sites to check: the network's homepage, and each town in its sitemap index
// (<loc>https://gloucester-ma.publick.org/sitemap.xml</loc>). Throws if the sitemap can't be read.
export async function siteHosts(env) {
  const sitemap = env.SITEMAP_URL || "https://publick.org/sitemap.xml";
  const home = new URL(sitemap).host;
  const response = await env.SITES.fetch(sitemap);
  if (!response.ok) throw new Error(`${sitemap}: HTTP ${response.status}`);
  const hosts = [...(await response.text()).matchAll(/<loc>\s*https:\/\/([^/<\s]+)\//g)].map((m) => m[1]);
  return [home, ...new Set(hosts.filter((h) => h !== home))];
}

// Why a site's homepage didn't load, or null if it did.
export async function siteProblem(env, host) {
  try {
    const response = await env.SITES.fetch(`https://${host}/`);
    const body = await response.text();
    if (response.status !== 200) return `HTTP ${response.status}`;
    if (!body.includes("</html>")) return "an empty or partial page";
    return null;
  } catch (error) {
    return error.message;
  }
}

// Each site that doesn't load, tried twice: [{host, problem}].
export async function downSites(env, wait = (ms) => new Promise((done) => setTimeout(done, ms))) {
  let hosts;
  try {
    hosts = await siteHosts(env);
  } catch (error) {
    // No sitemap: the homepage at least, which serves it.
    const home = new URL(env.SITEMAP_URL || "https://publick.org/sitemap.xml").host;
    return [{ host: home, problem: `its sitemap didn't load (${error.message})` }];
  }
  const first = (await Promise.all(hosts.map(async (host) => ({ host, problem: await siteProblem(env, host) }))))
    .filter((s) => s.problem);
  if (!first.length) return [];
  await wait(RETRY_MS);
  return (await Promise.all(first.map(async ({ host }) => ({ host, problem: await siteProblem(env, host) }))))
    .filter((s) => s.problem);
}

function downBody(down, now) {
  const list = down.map((s) => `- ${s.host}: ${s.problem}`).join("\n");
  return `These Publick sites aren't loading, as of ${now.toISOString().slice(0, 16).replace("T", " ")} UTC ` +
    "(each tried twice, through the sites Worker):\n\n" + list + "\n\n" +
    "See the publick-sites Worker's logs on Cloudflare, and the RUNBOOK's rollback. The scheduler checks every hour, " +
    "keeps this list up to date, and closes this issue when every site loads again.";
}

// Opens the "site down" issue when a site doesn't load, updates its list when that changes, and closes it when all load.
export async function watchSites(env, now = new Date(), wait = undefined) {
  const down = await downSites(env, wait);
  const label = encodeURIComponent(DOWN_LABEL);
  const open = await (await ok(await github(env, `/repos/${env.REPOSITORY}/issues?state=open&labels=${label}`),
    "listing issues")).json();
  if (down.length && open.length === 0) {
    const made = await github(env, `/repos/${env.REPOSITORY}/labels`, {
      method: "POST",
      body: JSON.stringify({ name: DOWN_LABEL, color: "B60205", description: "A Publick site isn't loading" }),
    });
    if (!made.ok && made.status !== 422) await ok(made, "making the label");
    await ok(await github(env, `/repos/${env.REPOSITORY}/issues`, {
      method: "POST",
      body: JSON.stringify({
        title: down.length === 1 ? `${down[0].host} isn't loading` : `${down.length} Publick sites aren't loading`,
        body: downBody(down, now),
        labels: [DOWN_LABEL],
        ...(env.ALERT_ASSIGNEE ? { assignees: [env.ALERT_ASSIGNEE] } : {}),
      }),
    }), "opening the issue");
  } else if (down.length) {
    // Still down: the list is updated only when it changes, so the issue isn't rewritten every hour.
    const listed = (issue) => [...(issue.body || "").matchAll(/^- ([^:\s]+):/gm)].map((m) => m[1]).join(" ");
    const hosts = down.map((s) => s.host).join(" ");
    for (const issue of open.filter((i) => listed(i) !== hosts)) {
      await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${issue.number}`, {
        method: "PATCH", body: JSON.stringify({ body: downBody(down, now) }),
      }), "updating the issue");
    }
  } else {
    for (const issue of open) {
      await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${issue.number}/comments`, {
        method: "POST",
        body: JSON.stringify({ body: `Every site loaded at ${now.toISOString().slice(0, 16).replace("T", " ")} UTC.` }),
      }), "commenting");
      await ok(await github(env, `/repos/${env.REPOSITORY}/issues/${issue.number}`, {
        method: "PATCH", body: JSON.stringify({ state: "closed", state_reason: "completed" }),
      }), "closing the issue");
    }
  }
  return { down };
}

// Each Cron Trigger: the release, the engine pull request, the weekly digest, the uptime check, or a daily run
// and a check on the runs.
// For a daily run, one failing doesn't stop the other; either failing fails the invocation, so it
// shows in the Worker's logs.
export async function onSchedule(controller, env) {
  if (controller.cron === RELEASE_CRON) {
    return startWorkflow(env, env.ENGINE_REPOSITORY || "publick-org/publick-engine", env.RELEASE_WORKFLOW || "release.yml");
  }
  if (controller.cron === ENGINE_CRON) {
    return startWorkflow(env, env.REPOSITORY, env.ENGINE_WORKFLOW || "engine.yml");
  }
  if (controller.cron === UPTIME_CRON) {
    return watchSites(env, new Date(controller.scheduledTime));
  }
  if (controller.cron === DIGEST_CRON) {
    const now = new Date(controller.scheduledTime);
    try {
      return await sendDigests(env, now);
    } catch (error) {
      await reportDigestFailure(env, error, now);
      throw error;
    }
  }
  const results = await Promise.allSettled([startRun(env), watch(env, new Date(controller.scheduledTime))]);
  const failed = results.filter((r) => r.status === "rejected").map((r) => r.reason);
  if (failed.length) throw new AggregateError(failed, failed.map((e) => e.message).join("; "));
  return results.map((r) => r.value);
}
