// The network's scheduler (scheduler-index.js, a Worker of its own, apart from
// the one that serves the sites). On each of its Cron Triggers it starts the
// network's daily run through GitHub's API, as "Run workflow" does, and checks
// that daily runs are still finishing.
//
// GitHub starts scheduled workflows when it can, sometimes hours late, and can
// drop one; a Cron Trigger fires on time. A daily run takes only the towns that
// are due (pipeline.network plan --due-hours), so starting one every hour of
// the morning does no harm: a start with nothing due does nothing.
//
// If no town's daily run has finished for STALE_HOURS (the status page's
// last-daily-run), it opens an issue, assigned to the maintainer, so a network
// that has stopped altogether still reaches someone; it closes the issue when
// runs finish again.
//
// Environment (the network repository's wrangler.scheduler.toml): GITHUB_TOKEN,
// a secret (a fine-grained token for the network repository with Actions and
// Issues read and write); REPOSITORY, WORKFLOW, BRANCH, STATUS_URL, and
// ALERT_ASSIGNEE; and SITES, a service binding to the Worker that serves the
// sites, which the status page is read through.

export const STALE_HOURS = 30;
export const ALERT_LABEL = "network stopped";
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

// Each Cron Trigger: start a run, and check on the runs. One failing doesn't stop the other; either
// failing fails the invocation, so it shows in the Worker's logs.
export async function onSchedule(controller, env) {
  const results = await Promise.allSettled([startRun(env), watch(env, new Date(controller.scheduledTime))]);
  const failed = results.filter((r) => r.status === "rejected").map((r) => r.reason);
  if (failed.length) throw new AggregateError(failed, failed.map((e) => e.message).join("; "));
  return results.map((r) => r.value);
}
