// The weekly digest's email (pipeline/digest.py), through Buttondown's API
// (https://docs.buttondown.com/api-introduction): the signup form's address,
// which the sites Worker (sites.js) answers, and the Sunday send, which the
// scheduler (scheduler.js) starts.
//
// Every town's readers are on one Buttondown newsletter, each tagged with the
// town and language they signed up for ("gloucester-ma-en"); a town's issue is
// sent to its tag alone. Tags are a paid Buttondown feature.
//
// Signing up: the form on a town's /digest/ page posts here, to its own
// address (the pages' Content-Security-Policy allows a form nowhere else).
// A new address is added unconfirmed, and Buttondown emails it to confirm; an
// address already confirmed gets the town's tag; one that unsubscribed, or
// never confirmed, is asked to confirm again; one Buttondown won't send to
// (bounced, marked as spam) is left alone. Every one is answered the same
// way, so the form never says whether an address is subscribed. Buttondown is
// given the reader's IP address, for its firewall, which flags an address
// submitting many signups: the form has no CAPTCHA.
//
// Sending: on each DIGEST_CRON trigger the scheduler reads each town in
// DIGEST_TOWNS (its hostname; none, and nothing is sent) for its newest issue
// (/digest/feed.xml), and sends it once its date has passed (Sunday at 5:30
// PM, the town's time), within SEND_WINDOW_HOURS: an issue isn't sent late, nor
// twice (an email with its subject sent since shortly before its date). Each
// run makes one request for each town, and a few more for each town sending;
// Workers on the free plan may make 50 a run, so about 20 towns.
//
// Environment: BUTTONDOWN_SUBSCRIBE_KEY (the sites Worker's: subscribers read
// and write, sending disabled) and BUTTONDOWN_SEND_KEY (the scheduler's:
// emails read and write, sending enabled), secrets; DIGEST_TOWNS.

export const API = "https://api.buttondown.com/v1";
export const SIGNUP_PATH = "/digest/subscribe";
// Every hour of Sunday and Monday, UTC: Sunday evening in every US time zone. Days by name:
// Cloudflare numbers them 1 (Sunday) to 7, not cron's usual 0 to 6, and refuses a 0.
export const DIGEST_CRON = "35 * * * SUN,MON";
export const SEND_WINDOW_HOURS = 6;
// The digest's languages: English for now.
export const LANGUAGES = ["en"];
// Subscribers Buttondown won't send to, or who asked not to be added again: left as they are.
const LEAVE_ALONE = ["blocked", "complained", "undeliverable", "removed"];
// Subscribers asked to confirm (again) when they sign up: they unsubscribed, or never confirmed.
const CONFIRM_AGAIN = ["unsubscribed", "unactivated"];
const EMAIL = /^[^\s@<>()",;:]+@[^\s@<>()",;:]+\.[^\s@<>()",;:]+$/;

function buttondown(key, path, init = {}) {
  return fetch(path.startsWith("https://") ? path : `${API}${path}`, {
    ...init,
    headers: {
      Authorization: `Token ${key}`,
      "User-Agent": "publick",
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...init.headers,
    },
  });
}

async function ok(response, what) {
  if (!response.ok) throw new Error(`${what}: HTTP ${response.status} ${await response.text()}`);
  return response;
}

// As ok(), for a reader's address: the error names Buttondown's code alone, never its text, which
// may quote the address, so the Worker's logs keep none.
async function okQuietly(response, what) {
  if (response.ok) return response;
  let code = "";
  try {
    code = String((await response.json()).code || "").replace(/[^a-z_]/g, "");
  } catch {
    // No code to give.
  }
  throw new Error(`${what}: HTTP ${response.status}${code ? ` ${code}` : ""}`);
}

// The town a site's hostname is for: gloucester-ma.publick.org -> gloucester-ma.
export function townOf(host) {
  return host.toLowerCase().split(".")[0];
}

export function tagName(town, lang) {
  return `${town}-${lang}`;
}

// ---- Signing up -------------------------------------------------------------

// Adds an address to the newsletter with a town's tag, as the top of this file says.
// Returns what was done: "added", "tagged", "confirm_again", or "left_alone".
export async function addSubscriber(key, email, tag, { ip = null, referrer = "" } = {}) {
  const fields = { email_address: email, tags: [tag], referrer_url: referrer, ...(ip ? { ip_address: ip } : {}) };
  const found = await buttondown(key, `/subscribers/${encodeURIComponent(email)}`);
  if (found.status === 404) {
    // Unconfirmed, the default: Buttondown asks the address to confirm.
    await okQuietly(await buttondown(key, "/subscribers", { method: "POST", body: JSON.stringify(fields) }), "adding a subscriber");
    return "added";
  }
  const subscriber = await (await okQuietly(found, "looking up a subscriber")).json();
  if (LEAVE_ALONE.includes(subscriber.type)) return "left_alone";
  const again = CONFIRM_AGAIN.includes(subscriber.type);
  // "add" merges the tag into the subscriber's others. Without a type it would also take back an
  // unsubscribe, unasked, so one who left (or never confirmed) is asked to confirm again.
  await okQuietly(await buttondown(key, "/subscribers", {
    method: "POST",
    headers: { "X-Buttondown-Collision-Behavior": "add" },
    body: JSON.stringify({ ...fields, ...(again ? { type: "unactivated" } : {}) }),
  }), "tagging a subscriber");
  return again ? "confirm_again" : "tagged";
}

// The answer to the signup form: on to the town's "check your email" page, or its "that didn't work" page.
export async function subscribe(request, env, url) {
  const answer = (page) => new Response(null, {
    status: 303,
    headers: { Location: `${url.origin}/digest/${page}/`, "Cache-Control": "no-store" },
  });
  // Only the town's own pages post here.
  if (request.headers.get("Origin") !== url.origin) return new Response("Forbidden", { status: 403 });
  let form;
  try {
    form = await request.formData();
  } catch {
    return answer("problem");
  }
  // A field people don't see, which bots fill in: answered as if done, and nothing sent.
  if (form.get("website")) return answer("thanks");
  const email = String(form.get("email") || "").trim();
  if (email.length > 254 || !EMAIL.test(email)) return answer("problem");
  const lang = LANGUAGES.includes(form.get("lang")) ? form.get("lang") : LANGUAGES[0];
  if (!env.BUTTONDOWN_SUBSCRIBE_KEY) {
    console.error("Digest signup: BUTTONDOWN_SUBSCRIBE_KEY isn't set.");
    return answer("problem");
  }
  try {
    await addSubscriber(env.BUTTONDOWN_SUBSCRIBE_KEY, email, tagName(townOf(url.hostname), lang), {
      ip: request.headers.get("CF-Connecting-IP"),
      referrer: request.headers.get("Referer") || `${url.origin}/digest/`,
    });
  } catch (error) {
    console.error(`Digest signup on ${url.hostname}: ${error.message}`);
    return answer("problem");
  }
  return answer("thanks");
}

// ---- Sending ----------------------------------------------------------------

function unescapeXml(text) {
  return text.replace(/&(lt|gt|quot|apos|amp|#\d+|#x[0-9a-f]+);/gi, (_, e) => {
    const named = { lt: "<", gt: ">", quot: '"', apos: "'", amp: "&" }[e.toLowerCase()];
    if (named) return named;
    return String.fromCodePoint(e[1].toLowerCase() === "x" ? parseInt(e.slice(2), 16) : parseInt(e.slice(1), 10));
  });
}

// The newest issue in a town's /digest/feed.xml (pipeline/build_site.py write_digest_feed): its
// subject (the site's name and the issue's title), link, due date, and body; null for a feed with none.
export function newestIssue(feed) {
  const channel = feed.match(/<channel>\s*<title>([^<]*)<\/title>/);
  const item = feed.match(/<item><title>([^<]*)<\/title><link>([^<]*)<\/link>[\s\S]*?<pubDate>([^<]*)<\/pubDate><description>([^<]*)<\/description><\/item>/);
  if (!channel || !item) return null;
  // The channel is "<site name>: <the feed's name>".
  const site = unescapeXml(channel[1]).replace(/: [^:]*$/, "");
  return {
    subject: `${site}: ${unescapeXml(item[1])}`,
    link: unescapeXml(item[2]),
    due: new Date(item[3]),
    body: unescapeXml(item[4]),
  };
}

// Every page of a list from Buttondown's API.
async function listAll(key, path, what) {
  const results = [];
  for (let next = path, pages = 0; next && pages < 20; pages++) {
    const page = await (await ok(await buttondown(key, next), what)).json();
    results.push(...page.results);
    next = page.next;
  }
  return results;
}

// Sends one town's newest issue if it's due and not sent yet. Returns what was done.
export async function sendTown(env, host, now, cache) {
  const response = await env.SITES.fetch(`https://${host}/digest/feed.xml`);
  if (!response.ok) throw new Error(`its feed: HTTP ${response.status}`);
  const issue = newestIssue(await response.text());
  if (!issue || Number.isNaN(issue.due.getTime())) return { host, done: "no_issue" };
  const late = now - issue.due;
  if (late < 0 || late > SEND_WINDOW_HOURS * 3600_000) return { host, done: "not_due", subject: issue.subject };
  const key = env.BUTTONDOWN_SEND_KEY;
  // Sent already: an email with this subject, made since shortly before the issue was due.
  if (!cache.recent) {
    const since = new Date(now - (SEND_WINDOW_HOURS + 24) * 3600_000).toISOString();
    cache.recent = await listAll(key, `/emails?creation_date__start=${encodeURIComponent(since)}&excluded_fields=body`,
      "listing recent emails");
  }
  if (cache.recent.some((e) => e.subject === issue.subject && e.status !== "deleted")) {
    return { host, done: "already_sent", subject: issue.subject };
  }
  cache.tags ??= await listAll(key, "/tags?page_size=100", "listing tags");
  const tag = cache.tags.find((t) => t.name === tagName(townOf(host), "en"));
  // No tag: no one has signed up for this town yet.
  if (!tag) return { host, done: "no_subscribers", subject: issue.subject };
  const monday = issue.link.match(/\/digest\/(\d{4}-\d{2}-\d{2})\/?$/);
  const email = {
    subject: issue.subject,
    body: `<!-- buttondown-editor-mode: fancy -->\n${issue.body}`,
    status: "about_to_send",
    // Only the town's own readers: an email without this would go to every town's.
    filters: { predicate: "and", groups: [], filters: [{ field: "subscriber.tags", operator: "contains", value: tag.id }] },
    // The town's site is the archive, and its pages take corrections; comments go through the site's contact.
    archival_mode: "disabled",
    commenting_mode: "disabled",
    canonical_url: issue.link,
    ...(monday ? { slug: `${townOf(host)}-${monday[1]}` } : {}),
    metadata: { publick_issue: issue.link },
  };
  if (!email.filters.filters.length || !email.filters.filters[0].value) throw new Error("an email without its town's tag");
  // Buttondown asks each key once to confirm it means to send (API version 2026-04-01).
  await ok(await buttondown(key, "/emails", {
    method: "POST", headers: { "X-Buttondown-Live-Dangerously": "true" }, body: JSON.stringify(email),
  }), "sending");
  cache.recent.push({ subject: issue.subject, status: "about_to_send" });
  return { host, done: "sent", subject: issue.subject };
}

// The digest's run: each town in DIGEST_TOWNS. One town failing doesn't stop the others; any failing
// fails the run, naming each.
export async function sendDigests(env, now = new Date()) {
  const towns = (env.DIGEST_TOWNS || "").split(/[\s,]+/).filter(Boolean);
  if (!towns.length) return [];
  if (!env.BUTTONDOWN_SEND_KEY) throw new Error("DIGEST_TOWNS names towns, but BUTTONDOWN_SEND_KEY isn't set");
  const cache = {};
  const results = [];
  const failed = [];
  for (const host of towns) {
    try {
      const result = await sendTown(env, host, now, cache);
      console.log(`Digest ${host}: ${result.done}${result.subject ? ` (${result.subject})` : ""}`);
      results.push(result);
    } catch (error) {
      failed.push(new Error(`${host}: ${error.message}`));
    }
  }
  if (failed.length) throw new AggregateError(failed, failed.map((e) => e.message).join("; "));
  return results;
}
