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
// submitting many signups: the form has no CAPTCHA. A signup the firewall blocks
// is sent once more past it (postSubscriber). With SIGNUP_LIMITER bound, the
// Worker also limits how often one IP address, and one email address, can sign
// up (withinLimits).
//
// Sending: on each DIGEST_CRON trigger the scheduler reads each town in
// DIGEST_TOWNS (its hostname; none, and nothing is sent) for its newest issue
// (/digest/feed.xml), and sends it once its date has passed (Sunday at 5:30
// PM, the town's time), within SEND_WINDOW_HOURS: an issue isn't sent late, nor
// twice (an email with its subject sent since shortly before its date). A town
// whose newest issue is still last week's at this week's last try fails the run,
// so a week skipped (its Sunday build didn't publish) is seen. Each
// run makes one request for each town, and a few more for each town sending.
//
// Testing: sendTest() sends an issue at once to the addresses given and no
// subscriber, run by hand (worker/digest-test.js).
//
// Environment: BUTTONDOWN_SUBSCRIBE_KEY (the sites Worker's: subscribers read
// and write, sending disabled) and BUTTONDOWN_SEND_KEY (the scheduler's:
// emails read and write, sending enabled), secrets; DIGEST_TOWNS; and, optionally,
// SIGNUP_LIMITER, a rate limiting binding for the sites Worker.

export const API = "https://api.buttondown.com/v1";
export const SIGNUP_PATH = "/digest/subscribe";
// Every hour of Sunday and Monday, UTC: Sunday evening in every US time zone. Days by name:
// Cloudflare numbers them 1 (Sunday) to 7, not cron's usual 0 to 6, and refuses a 0.
export const DIGEST_CRON = "35 * * * SUN,MON";
export const SEND_WINDOW_HOURS = 6;
// The digest's languages: English for now.
export const LANGUAGES = ["en"];
// The start of a test's subject, so it's never mistaken for (or taken as) the issue sent.
export const TEST_PREFIX = "[Test] ";
// Buttondown's email template: "naked", none at all. Its others end each email with the issue's
// number, counted across every town, and a link to subscribe on Buttondown's own page, where a
// signup gets no town's tag and so is never sent an issue. The issue's own footer (the engine's
// digest_email.html) has the unsubscribe link and postal address in their place.
export const TEMPLATE = "naked";
// Buttondown's tag for each reader's own unsubscribe link: without it an email has none.
export const UNSUBSCRIBE_TAG = "{{ unsubscribe_url }}";
// The footer's postal address until it's filled in (MAILING_ADDRESS, the engine's pipeline/digest.py).
export const ADDRESS_PLACEHOLDER = "[mailing address]";
// A test's draft is deleted by the next test made this long after it, by when it's sent.
const TEST_KEEP_MINUTES = 10;
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

// Buttondown's code for an error ("email_invalid"), or "" for none.
async function errorCode(response) {
  try {
    return String((await response.json()).code || "").replace(/[^a-z_]/g, "");
  } catch {
    return "";
  }
}

// As ok(), for a reader's address: the error names Buttondown's code alone, never its text, which
// may quote the address, so the Worker's logs keep none.
async function okQuietly(response, what) {
  if (response.ok) return response;
  const code = await errorCode(response);
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

// Buttondown's firewall blocks a signup it judges suspicious (subscriber_blocked), as it does every
// signup from an IP address that has made several lately: a household's, or one a mobile network
// shares among many phones. A blocked signup is sent once more with this header, which skips the
// firewall, and which Buttondown allows five times an hour (then 429). The Worker's own checks (the
// hidden field, withinLimits) have already passed, and the address must still confirm.
export const BYPASS_FIREWALL = "X-Buttondown-Bypass-Firewall";

// Posts a subscriber, and, if the firewall blocks it, once more past the firewall.
async function postSubscriber(key, fields, headers, what) {
  const post = (extra) => buttondown(key, "/subscribers", {
    method: "POST", headers: { ...headers, ...extra }, body: JSON.stringify(fields),
  });
  let response = await post({});
  if (response.status === 400 && (await errorCode(response.clone())) === "subscriber_blocked") {
    console.log(`Digest signup: ${what} was blocked by Buttondown's firewall, so it's sent again past it`);
    response = await post({ [BYPASS_FIREWALL]: "true" });
  }
  return okQuietly(response, what);
}

// Adds an address to the newsletter with a town's tag, as the top of this file says.
// Returns what was done: "added", "tagged", "confirm_again", or "left_alone".
export async function addSubscriber(key, email, tag, { ip = null, referrer = "" } = {}) {
  const fields = { email_address: email, tags: [tag], referrer_url: referrer, ...(ip ? { ip_address: ip } : {}) };
  const found = await buttondown(key, `/subscribers/${encodeURIComponent(email)}`);
  if (found.status === 404) {
    // Unconfirmed, the default: Buttondown asks the address to confirm.
    await postSubscriber(key, fields, {}, "adding a subscriber");
    return "added";
  }
  const subscriber = await (await okQuietly(found, "looking up a subscriber")).json();
  if (LEAVE_ALONE.includes(subscriber.type)) return "left_alone";
  const again = CONFIRM_AGAIN.includes(subscriber.type);
  // "add" merges the tag into the subscriber's others. Without a type it would also take back an
  // unsubscribe, unasked, so one who left (or never confirmed) is asked to confirm again.
  await postSubscriber(key, { ...fields, ...(again ? { type: "unactivated" } : {}) },
    { "X-Buttondown-Collision-Behavior": "add" }, "tagging a subscriber");
  return again ? "confirm_again" : "tagged";
}

// Whether a signup is within SIGNUP_LIMITER's limits (a Workers rate limiting binding, set in the
// network's wrangler.toml), counted for the visitor's IP address and, apart, for the address being
// signed up, so a script can't have Buttondown send someone confirmation after confirmation. The
// address is counted by its hash, so it isn't kept anywhere. Without the binding every signup is
// within limits (a site served without the network's wrangler.toml); with it, a limiter that fails
// lets none through, as a signup it can't count could be one too many.
export async function withinLimits(env, request, email) {
  if (!env.SIGNUP_LIMITER) return true;
  try {
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(email.toLowerCase()));
    const hash = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
    const keys = [`email:${hash}`, `ip:${request.headers.get("CF-Connecting-IP") || "unknown"}`];
    const results = await Promise.all(keys.map((key) => env.SIGNUP_LIMITER.limit({ key })));
    if (results.every((r) => r.success)) return true;
    console.error("Digest signup: over the rate limit");
    return false;
  } catch (error) {
    console.error(`Digest signup: the rate limit couldn't be checked, so the signup wasn't passed on: ${error.message}`);
    return false;
  }
}

// The pages the form leads to in a language other than English, which the build makes when the form is on that
// language's pages: es/digest/thanks/ for Spanish. (The digest itself, and its tag, are in LANGUAGES alone.)
export function answerPrefix(lang, manifest) {
  const code = String(lang || "");
  return /^[a-z]{2}$/.test(code) && code !== "en" && manifest?.files?.[`${code}/digest/thanks/index.html`] ? `/${code}` : "";
}

// The answer to the signup form: on to the town's "check your email" page, or its "that didn't work" page, in
// the language of the page the form was on, where the site has them in it (answerPrefix).
export async function subscribe(request, env, url, manifest = null) {
  let prefix = "";
  const answer = (page) => new Response(null, {
    status: 303,
    headers: { Location: `${url.origin}${prefix}/digest/${page}/`, "Cache-Control": "no-store" },
  });
  // Only the town's own pages post here.
  if (request.headers.get("Origin") !== url.origin) return new Response("Forbidden", { status: 403 });
  let form;
  try {
    form = await request.formData();
  } catch {
    return answer("problem");
  }
  prefix = answerPrefix(form.get("lang"), manifest);
  // A field people don't see, which bots fill in: answered as if done, and nothing sent.
  if (form.get("website")) return answer("thanks");
  const email = String(form.get("email") || "").trim();
  if (email.length > 254 || !EMAIL.test(email)) return answer("problem");
  const lang = LANGUAGES.includes(form.get("lang")) ? form.get("lang") : LANGUAGES[0];
  if (!(await withinLimits(env, request, email))) return answer("problem");
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

// An issue as Buttondown takes an email's body: the HTML as it is, in its "fancy" editor's mode. One
// without an unsubscribe link isn't sent, nor tested.
function emailBody(issue) {
  if (!issue.body.includes(UNSUBSCRIBE_TAG)) throw new Error(`the issue has no unsubscribe link (${UNSUBSCRIBE_TAG})`);
  return `<!-- buttondown-editor-mode: fancy -->\n${issue.body}`;
}

// Sends one town's newest issue if it's due and not sent yet. Returns what was done.
export async function sendTown(env, host, now, cache) {
  const response = await env.SITES.fetch(`https://${host}/digest/feed.xml`);
  if (!response.ok) throw new Error(`its feed: HTTP ${response.status}`);
  const issue = newestIssue(await response.text());
  if (!issue || Number.isNaN(issue.due.getTime())) return { host, done: "no_issue" };
  const late = now - issue.due;
  // The newest issue is last week's at this week's last try: this week's never came, and the week is
  // skipped. Said once, so it isn't skipped unseen. (Within an hour: the week clocks change, a week
  // isn't 168 hours, and this says so an hour early or late.)
  const sinceNext = late - 7 * 24 * 3600_000;
  if (sinceNext >= (SEND_WINDOW_HOURS - 1) * 3600_000 && sinceNext < SEND_WINDOW_HOURS * 3600_000) {
    throw new Error(`this week's issue isn't in its feed by its last try (the newest is "${issue.subject}"), so this ` +
      "week isn't sent: the town's Sunday build may not have published, or the week had no meetings and no new minutes");
  }
  if (late < 0 || late > SEND_WINDOW_HOURS * 3600_000) return { host, done: "not_due", subject: issue.subject };
  const key = env.BUTTONDOWN_SEND_KEY;
  // Sent already: an email with this subject, made since shortly before the issue was due. Buttondown
  // takes a date here, not a time (it refused one on 2026-10-11), so the day before, from its start.
  if (!cache.recent) {
    const since = new Date(now - (SEND_WINDOW_HOURS + 24) * 3600_000).toISOString().slice(0, 10);
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
  if (issue.body.includes(ADDRESS_PLACEHOLDER)) {
    throw new Error("the issue's footer has no mailing address yet (MAILING_ADDRESS in the engine's pipeline/digest.py)");
  }
  const monday = issue.link.match(/\/digest\/(\d{4}-\d{2}-\d{2})\/?$/);
  const email = {
    subject: issue.subject,
    body: emailBody(issue),
    template: TEMPLATE,
    status: "about_to_send",
    // Only the town's own readers: an email without this would go to every town's.
    filters: { predicate: "and", groups: [], filters: [{ field: "subscriber.tags", operator: "contains", value: tag.id }] },
    // The town's site is the archive, and its pages take corrections; comments go through the site's contact.
    archival_mode: "disabled",
    commenting_mode: "disabled",
    canonical_url: issue.link,
    ...(monday ? { slug: `${townOf(host)}-${monday[1]}` } : {}),
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

// ---- Testing ----------------------------------------------------------------

// Deletes the earlier tests' drafts, but for any made in the last few minutes (by a test still sending).
// Returns how many were deleted.
export async function deleteTests(key, now = new Date()) {
  const since = now - TEST_KEEP_MINUTES * 60_000;
  const drafts = await listAll(key, "/emails?status=draft&excluded_fields=body", "listing drafts");
  const old = drafts.filter((e) => (e.subject || "").startsWith(TEST_PREFIX) && new Date(e.creation_date) < since);
  for (const draft of old) {
    await ok(await buttondown(key, `/emails/${encodeURIComponent(draft.id)}`, { method: "DELETE" }), "deleting an earlier test");
  }
  return old.length;
}

// Sends an issue now, as it would go on Sunday, to the addresses given and no subscriber: a draft,
// with "[Test] " before its subject, sent to those addresses alone. Returns the subject.
export async function sendTest(key, issue, recipients) {
  if (!recipients.length) throw new Error("no addresses to send the test to");
  const subject = `${TEST_PREFIX}${issue.subject}`;
  const draft = await (await ok(await buttondown(key, "/emails", {
    method: "POST",
    body: JSON.stringify({ subject, body: emailBody(issue), template: TEMPLATE, status: "draft", archival_mode: "disabled",
                           commenting_mode: "disabled", canonical_url: issue.link }),
  }), "making the test's draft")).json();
  // Quietly: Buttondown's answer may quote an address.
  await okQuietly(await buttondown(key, `/emails/${encodeURIComponent(draft.id)}/send-draft`, {
    method: "POST", headers: { "X-Buttondown-Live-Dangerously": "true" }, body: JSON.stringify({ recipients }),
  }), "sending the test");
  return subject;
}
