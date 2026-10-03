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
// Environment (the network repository's wrangler.scheduler.toml): GITHUB_TOKEN,
// a secret (a fine-grained token for the network repository with Actions and
// Issues read and write, and for the engine repository with Actions read and
// write); REPOSITORY, WORKFLOW, BRANCH, STATUS_URL, and ALERT_ASSIGNEE;
// ENGINE_REPOSITORY, RELEASE_WORKFLOW, and ENGINE_WORKFLOW; and SITES, a
// service binding to the Worker that serves the sites, which the status page
// is read through.

export const STALE_HOURS = 30;
// The Cron Triggers (as wrangler.scheduler.toml writes them) that start the release and the engine
// pull request; every other one starts a daily run.
export const RELEASE_CRON = "20 8 * * *";
export const ENGINE_CRON = "40 8 * * *";
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

// Each Cron Trigger: the release, the engine pull request, or a daily run and a check on the runs.
// For a daily run, one failing doesn't stop the other; either failing fails the invocation, so it
// shows in the Worker's logs.
export async function onSchedule(controller, env) {
  if (controller.cron === RELEASE_CRON) {
    return startWorkflow(env, env.ENGINE_REPOSITORY || "publick-org/publick-engine", env.RELEASE_WORKFLOW || "release.yml");
  }
  if (controller.cron === ENGINE_CRON) {
    return startWorkflow(env, env.REPOSITORY, env.ENGINE_WORKFLOW || "engine.yml");
  }
  const results = await Promise.allSettled([startRun(env), watch(env, new Date(controller.scheduledTime))]);
  const failed = results.filter((r) => r.status === "rejected").map((r) => r.reason);
  if (failed.length) throw new AggregateError(failed, failed.map((e) => e.message).join("; "));
  return results.map((r) => r.value);
}
