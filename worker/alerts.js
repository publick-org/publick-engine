// Email alerts for followed addresses: a reader follows an address on the street lookup and is emailed
// when a new agenda or minutes line names it (pipeline/mentions.py finds them; the build lists the last
// 30 days' in /streets/mentions.json). Answered by the sites Worker (sites.js), on a site whose build has
// the alerts pages (alerts/thanks/), with its D1 database (ALERTS_DB) and email key (POSTMARK_TOKEN).
//
// Following: the street lookup's form posts the house number, the street and an address to FOLLOW_PATH.
// The follow is kept unconfirmed and the address is emailed a link to confirm it: a page whose button
// posts to CONFIRM_PATH, so a mail scanner opening the link confirms nothing. Every answer is the same
// "check your email" page, so the form never says whether an address follows anything. An address may
// follow at most MAX_FOLLOWS addresses, in every town, and gets at most CONFIRMS_PER_DAY
// confirmation emails a day, so the form can't be used to fill a stranger's inbox; an unconfirmed follow
// is deleted after UNCONFIRMED_DAYS. The signup's rate limit (digest.js withinLimits) applies here too.
//
// Managing: every email links the reader's own page (MANAGE_PATH), by a token made from their address
// with ALERTS_TOKEN_SECRET (an HMAC, so every email's link is the same and none stops working), which
// lists what they follow and removes one or all; and has a one-click unsubscribe (RFC 8058) at
// UNSUBSCRIBE_PATH, which removes all. Removing deletes the rows: nothing about a follow is kept after.
//
// Sending: after a town's site is published, the network's run posts to SEND_PATH on the town's host,
// with ALERTS_HOOK_SECRET. The Worker reads that site's mentions.json and emails each reader the new
// mentions of the addresses they follow, posted since the town's last send: one email a reader, and a
// town sends at most once a day (a second publish the same day waits for the next). Each delivery is
// recorded before it's sent, so a send tried again never emails a reader twice for the same day.
//
// Tokens are random, and only their SHA-256 is stored. An email address is stored in lower case, with
// the town, the followed key and the language, and nothing else: no IP address, no opens or clicks.
//
// Environment: ALERTS_DB (D1, schema in alerts.sql), POSTMARK_TOKEN (a Postmark server's token, a
// secret), ALERTS_FROM (the sending address, "alerts@alerts.example.org"; each email is from its site's
// name at it, "Salem Publick <alerts@...>"), ALERTS_HOOK_SECRET and
// ALERTS_TOKEN_SECRET (secrets),
// optionally ALERTS_STREAM (Postmark's message stream, "outbound" by default), ALERTS_MAILING_ADDRESS
// (the footer's postal address) and SIGNUP_LIMITER (digest.js).

import { EMAIL, smallForm, withinLimits } from "./digest.js";

export const FOLLOW_PATH = "/alerts/follow";
export const CONFIRM_PATH = "/alerts/confirm";
export const MANAGE_PATH = "/alerts/manage";
export const UNSUBSCRIBE_PATH = "/alerts/unsubscribe";
export const SEND_PATH = "/alerts/send";
export const PATHS = [FOLLOW_PATH, CONFIRM_PATH, MANAGE_PATH, UNSUBSCRIBE_PATH, SEND_PATH];
// The page the build makes for a town with alerts on: without it, the town has none.
export const PAGE = "alerts/thanks/index.html";
export const MENTIONS = "streets/mentions.json";
export const MAX_FOLLOWS = 25;
export const UNCONFIRMED_DAYS = 7;
// The most confirmation emails an address gets in a day, however often a form names it.
export const CONFIRMS_PER_DAY = 5;
const DELIVERIES_KEEP_DAYS = 30;
// Postmark's batch call takes at most 500 emails, and D1 at most 100 bound values a statement.
const BATCH = 500;
const BOUND = 90;
const NUMBER = /^\d{1,6}[A-Z]?$/;
const STREET = /^[A-Z0-9][A-Z0-9 ]{1,60}$/;
const LANGUAGES = ["en"];

export function townOf(host) {
  return host.toLowerCase().split(".")[0];
}

// A street's folder in the feeds (pipeline/mentions.py slug()).
export function slug(street) {
  return street.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
}

export function enabled(env, manifest) {
  return Boolean(env.ALERTS_DB && manifest?.files?.[PAGE]);
}

function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function newToken() {
  return base64url(crypto.getRandomValues(new Uint8Array(24)));
}

export async function hashed(token) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(String(token)));
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function sameSecret(a, b) {
  if (!a || !b || a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

function json(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function daysAgo(now, days) {
  return new Date(now.getTime() - days * 86_400_000).toISOString();
}

// The site's mentions.json (pipeline/mentions.py recent()), read from the bucket by its blob.
export async function readMentions(env, manifest) {
  const entry = manifest?.files?.[MENTIONS];
  if (!entry) return null;
  const object = await env.SITES.get(`blobs/${entry.blob}`);
  return object ? JSON.parse(await object.text()) : null;
}

// ---- Email ------------------------------------------------------------------

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

// Postmark's error for a recipient it won't send to: one that bounced for good or marked an email as spam.
export const INACTIVE = 406;

// "Salem Publick" and ALERTS_FROM -> "Salem Publick <alerts@...>"; an ALERTS_FROM with its own name as it is.
export function sender(env, name) {
  const from = String(env.ALERTS_FROM || "");
  if (from.includes("<") || !name) return from;
  return `"${String(name).replace(/["\\\r\n]/g, "")}" <${from}>`;
}

// Sends emails ({to, from, subject, text, html, unsubscribe}) through the provider: Postmark's batch API.
// Another provider is another function here. Returns {failed, inactive}: the addresses not accepted, to try
// again later, and those the provider won't send to again, whose alerts are stopped.
export async function sendEmails(env, emails, fetchImpl = fetch) {
  const failed = [], inactive = [];
  for (let i = 0; i < emails.length; i += BATCH) {
    const chunk = emails.slice(i, i + BATCH);
    const response = await fetchImpl("https://api.postmarkapp.com/email/batch", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-Postmark-Server-Token": env.POSTMARK_TOKEN },
      body: JSON.stringify(chunk.map((e) => ({
        From: sender(env, e.from), To: e.to, Subject: e.subject, TextBody: e.text, HtmlBody: e.html,
        MessageStream: env.ALERTS_STREAM || "outbound", TrackOpens: false, TrackLinks: "None",
        Headers: e.unsubscribe ? [{ Name: "List-Unsubscribe", Value: `<${e.unsubscribe}>` },
          { Name: "List-Unsubscribe-Post", Value: "List-Unsubscribe=One-Click" }] : [],
      }))),
    });
    if (!response.ok) {
      // The status alone: Postmark's text may quote an address, and the logs keep none.
      console.error(`Alerts: the email provider answered HTTP ${response.status}`);
      failed.push(...chunk.map((e) => e.to));
      continue;
    }
    const results = await response.json();
    chunk.forEach((e, n) => {
      const code = results[n]?.ErrorCode;
      if (code === INACTIVE) inactive.push(e.to);
      else if (code !== 0) failed.push(e.to);
    });
  }
  return { failed, inactive };
}

function footer(env, manage) {
  const address = env.ALERTS_MAILING_ADDRESS ? `\nPublick · ${env.ALERTS_MAILING_ADDRESS}` : "";
  return {
    text: `You get this email because you follow addresses on this site. Change or stop them: ${manage}${address}`,
    html: `<p style="font-size:13px;color:#545c65">You get this email because you follow addresses on this site. `
      + `<a href="${escapeHtml(manage)}">Change or stop them</a>.${address ? `<br>${escapeHtml(address.trim())}` : ""}</p>`,
  };
}

export function confirmEmail(env, site, origin, email, label, token) {
  const link = `${origin}/alerts/confirm/?t=${token}`;
  const text = `Someone, we hope you, asked to follow ${label} on ${site}: an email when a new agenda or minutes line names it.\n\n`
    + `To start, confirm here: ${link}\n\nIf it wasn't you, do nothing: nothing is sent, and the request is deleted in ${UNCONFIRMED_DAYS} days.`
    + (env.ALERTS_MAILING_ADDRESS ? `\n\nPublick · ${env.ALERTS_MAILING_ADDRESS}` : "");
  const html = `<p>Someone, we hope you, asked to follow <strong>${escapeHtml(label)}</strong> on ${escapeHtml(site)}: an email when a new agenda or minutes line names it.</p>`
    + `<p><a href="${escapeHtml(link)}">Confirm to start</a></p>`
    + `<p style="font-size:13px;color:#545c65">If it wasn't you, do nothing: nothing is sent, and the request is deleted in ${UNCONFIRMED_DAYS} days.`
    + (env.ALERTS_MAILING_ADDRESS ? `<br>Publick · ${escapeHtml(env.ALERTS_MAILING_ADDRESS)}` : "") + "</p>";
  return { to: email, from: site, subject: `Confirm: follow ${label}`, text, html };
}

// "62 MAIN ST" -> "62 Main Street", as the site writes it (mentions.json gives each address's name).
function label(key, mention) {
  return mention?.names?.[mention.addresses.indexOf(key)] || key;
}

const WHEN = { month: "long", day: "numeric", year: "numeric", timeZone: "UTC" };

export function alertEmail(env, site, origin, email, items, manage, unsubscribe) {
  const lines = items.map(({ key, mention }) => {
    const when = new Date(`${mention.date}T12:00:00Z`).toLocaleDateString("en-US", WHEN);
    const what = mention.kind === "agenda" ? `agenda for ${when}` : `minutes of ${when}`;
    const link = `${origin}${mention.url}?ref=alert-email`;
    // A line that isn't about the address: its page (alerts.html) is counted, and leads to the corrections.
    const wrong = `${origin}/alerts/wrong/?ref=alert-email`;
    return { key, mention, what, link, wrong, name: label(key, mention) };
  });
  const names = [...new Set(lines.map((l) => l.name))];
  const subject = names.length === 1
    ? `${names[0]}: ${lines[0].mention.board}${lines.length > 1 ? ` and ${lines.length - 1} more` : ""}`
    : `${names.length} addresses you follow came up`;
  const end = footer(env, manage);
  const text = `New on ${site}, for the addresses you follow:\n\n`
    + lines.map((l) => `${l.name}\n${l.mention.board}, ${l.what}\n“${l.mention.line}”\n${l.link}\nNot about this address? ${l.wrong}`).join("\n\n")
    + `\n\nLines are as the documents give them.\n\n${end.text}`;
  const html = `<p>New on ${escapeHtml(site)}, for the addresses you follow:</p>`
    + lines.map((l) => `<p><strong>${escapeHtml(l.name)}</strong><br>${escapeHtml(l.mention.board)}, `
      + `<a href="${escapeHtml(l.link)}">${escapeHtml(l.what)}</a><br>“${escapeHtml(l.mention.line)}”<br>`
      + `<small><a href="${escapeHtml(l.wrong)}">Not about this address?</a></small></p>`).join("")
    + `<p style="font-size:13px;color:#545c65">Lines are as the documents give them.</p>${end.html}`;
  return { to: email, from: site, subject, text, html, unsubscribe };
}

// ---- The forms ----------------------------------------------------------------

async function form(request, url) {
  if (request.headers.get("Origin") !== url.origin) return null;
  if (!/^application\/x-www-form-urlencoded\b/i.test(request.headers.get("Content-Type") || "")) return null;
  try {
    return await smallForm(request);
  } catch {
    return null;
  }
}

// A reader's token, for their page and their unsubscribe link: the HMAC of their address, so it's the same
// in every email. Only its hash is stored, as the reader's row, made when first needed and deleted with
// their last follow.
export async function readerToken(env, email) {
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(env.ALERTS_TOKEN_SECRET),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const token = base64url(new Uint8Array(await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(email))));
  await env.ALERTS_DB.prepare("INSERT INTO readers (email, manage) VALUES (?1, ?2) ON CONFLICT (email) DO UPDATE SET manage = ?2")
    .bind(email, await hashed(token)).run();
  return token;
}

// "62 MAIN ST" -> "62 Main St".
export function keyLabel(key) {
  const [number, ...street] = String(key).split(" ");
  return `${number} ${streetLabel(street.join(" "))}`;
}

// "MAIN ST" -> "Main St": a street's key as a name, for an email, from nothing a form sent.
export function streetLabel(street) {
  return street.toLowerCase().replace(/\b[a-z]/g, (c) => c.toUpperCase()).replace(/\b(\d+)(St|Nd|Rd|Th)\b/g, (_, n, s) => n + s.toLowerCase());
}

export async function follow(request, env, url, manifest, now = new Date(), fetchImpl = fetch) {
  const answer = (page) => new Response(null, {
    status: 303, headers: { Location: `${url.origin}/alerts/${page}/`, "Cache-Control": "no-store" },
  });
  const fields = await form(request, url);
  if (!fields) return answer("problem");
  if (fields.get("website")) return answer("thanks");
  const email = String(fields.get("email") || "").trim().toLowerCase();
  const number = String(fields.get("number") || "").trim().toUpperCase();
  const street = String(fields.get("street") || "").trim().toUpperCase();
  const lang = LANGUAGES.includes(fields.get("lang")) ? fields.get("lang") : LANGUAGES[0];
  if (email.length > 254 || !EMAIL.test(email) || !NUMBER.test(number) || !STREET.test(street)) return answer("problem");
  // A street the documents name: one with a feed.
  if (!manifest.files[`streets/feeds/${slug(street)}.xml`]) return answer("problem");
  if (!(await withinLimits(env, request, email))) return answer("problem");
  if (!env.POSTMARK_TOKEN || !env.ALERTS_FROM || !env.ALERTS_TOKEN_SECRET) {
    console.error("Alerts: POSTMARK_TOKEN, ALERTS_FROM or ALERTS_TOKEN_SECRET isn't set.");
    return answer("problem");
  }
  const db = env.ALERTS_DB;
  const town = townOf(url.hostname);
  const key = `${number} ${street}`;
  const row = await db.prepare("SELECT confirmed_at FROM follows WHERE email = ?1 AND town = ?2 AND kind = 'address' AND key = ?3")
    .bind(email, town, key).first();
  if (row?.confirmed_at) return answer("thanks");
  if (!row) {
    const { count } = await db.prepare("SELECT COUNT(*) AS count FROM follows WHERE email = ?1").bind(email).first();
    if (count >= MAX_FOLLOWS) return answer("problem");
  }
  // Asked to confirm enough today: answered as done, and nothing more sent.
  const { asked } = await db.prepare("SELECT COUNT(*) AS asked FROM follows WHERE email = ?1 AND confirmed_at IS NULL AND created_at > ?2")
    .bind(email, daysAgo(now, 1)).first();
  if (asked >= CONFIRMS_PER_DAY) return answer("thanks");
  const token = newToken();
  await db.prepare(`INSERT INTO follows (email, town, kind, key, lang, token, created_at) VALUES (?1, ?2, 'address', ?3, ?4, ?5, ?6)
    ON CONFLICT (email, town, kind, key) DO UPDATE SET token = ?5, created_at = ?6, lang = ?4`)
    .bind(email, town, key, lang, await hashed(token), now.toISOString()).run();
  const mentions = await readMentions(env, manifest).catch(() => null);
  const site = mentions?.site || url.hostname;
  const name = `${number} ${streetLabel(street)}`;
  const { failed, inactive } = await sendEmails(env, [confirmEmail(env, site, url.origin, email, name, token)], fetchImpl);
  // An address the provider won't send to can't confirm: its follow isn't kept.
  if (inactive.length) await db.prepare("DELETE FROM follows WHERE email = ?1 AND confirmed_at IS NULL").bind(email).run();
  return answer(failed.length || inactive.length ? "problem" : "thanks");
}

export async function confirm(request, env, url) {
  const fields = await form(request, url);
  const token = fields?.get("t");
  if (!token) return json({ ok: false }, 400);
  const db = env.ALERTS_DB;
  const row = await db.prepare("SELECT email, key FROM follows WHERE token = ?1").bind(await hashed(token)).first();
  if (!row) return json({ ok: false }, 404);
  await db.prepare("UPDATE follows SET confirmed_at = COALESCE(confirmed_at, ?2) WHERE token = ?1")
    .bind(await hashed(token), new Date().toISOString()).run();
  return json({ ok: true, key: row.key, name: keyLabel(row.key) });
}

// The reader's page: {follows: [{town, key}]} for "list", and removes one ("remove": "<town>|<key>") or
// every one ("remove": "all").
export async function manage(request, env, url) {
  const fields = await form(request, url);
  const token = fields?.get("m");
  if (!token) return json({ ok: false }, 400);
  const db = env.ALERTS_DB;
  const reader = await db.prepare("SELECT email FROM readers WHERE manage = ?1").bind(await hashed(token)).first();
  if (!reader) return json({ ok: false }, 404);
  const remove = String(fields.get("remove") || "");
  if (remove === "all") {
    await forget(db, reader.email);
  } else if (remove) {
    const [town, key] = remove.split("|");
    await db.prepare("DELETE FROM follows WHERE email = ?1 AND town = ?2 AND key = ?3").bind(reader.email, town, key).run();
  }
  const { results } = await db.prepare(
    "SELECT town, key FROM follows WHERE email = ?1 AND confirmed_at IS NOT NULL ORDER BY town, key").bind(reader.email).all();
  return json({ ok: true, follows: results.map((f) => ({ ...f, name: keyLabel(f.key) })) });
}

async function forget(db, email) {
  await db.batch([
    db.prepare("DELETE FROM follows WHERE email = ?1").bind(email),
    db.prepare("DELETE FROM deliveries WHERE email = ?1").bind(email),
    db.prepare("DELETE FROM readers WHERE email = ?1").bind(email),
  ]);
}

// One-click unsubscribe (RFC 8058): a mail app posts "List-Unsubscribe=One-Click" here, with no Origin.
export async function unsubscribe(request, env, url) {
  const token = url.searchParams.get("m");
  if (!token) return new Response("Not found", { status: 404 });
  const reader = await env.ALERTS_DB.prepare("SELECT email FROM readers WHERE manage = ?1").bind(await hashed(token)).first();
  if (reader) await forget(env.ALERTS_DB, reader.email);
  return new Response("Unsubscribed.", { status: 200, headers: { "Cache-Control": "no-store" } });
}

// ---- Sending ----------------------------------------------------------------

// What a send did: {sent, failed, mentions, skipped?}.
export async function send(request, env, url, manifest, now = new Date(), fetchImpl = fetch) {
  const auth = (request.headers.get("Authorization") || "").replace(/^Bearer\s+/i, "");
  if (!sameSecret(auth, env.ALERTS_HOOK_SECRET || "")) return json({ ok: false }, 403);
  const db = env.ALERTS_DB;
  const town = townOf(url.hostname);
  const today = now.toISOString().slice(0, 10);
  // Tidy first: unconfirmed follows past their days, deliveries past theirs, readers who follow nothing.
  await db.batch([
    db.prepare("DELETE FROM follows WHERE confirmed_at IS NULL AND created_at < ?1").bind(daysAgo(now, UNCONFIRMED_DAYS)),
    db.prepare("DELETE FROM deliveries WHERE day < ?1").bind(daysAgo(now, DELIVERIES_KEEP_DAYS).slice(0, 10)),
    db.prepare("DELETE FROM readers WHERE email NOT IN (SELECT email FROM follows)"),
  ]);
  const last = await db.prepare("SELECT through, day FROM sends WHERE town = ?1").bind(town).first();
  if (last?.day === today) return json({ ok: true, skipped: "sent today", sent: 0 });
  const mentions = await readMentions(env, manifest);
  if (!mentions) return json({ ok: false, error: "no mentions.json" }, 404);
  // The first send starts from now: what came before was there when the reader followed it.
  const through = last?.through || now.toISOString();
  // Times are compared as times: a document's carries its town's offset ("-05:00"), a send's is UTC.
  const after = Date.parse(through);
  const fresh = mentions.mentions.filter((m) => Date.parse(m.posted) > after && m.addresses?.length);
  const newest = new Date(Math.max(after, ...mentions.mentions.map((m) => Date.parse(m.posted)))).toISOString();
  if (!fresh.length) {
    await db.prepare("INSERT INTO sends (town, through, day) VALUES (?1, ?2, NULL) ON CONFLICT (town) DO UPDATE SET through = ?2")
      .bind(town, newest).run();
    return json({ ok: true, sent: 0, mentions: 0 });
  }
  const byKey = new Map();
  for (const m of fresh) for (const key of m.addresses) byKey.set(key, [...(byKey.get(key) || []), m]);
  const keys = [...byKey.keys()];
  const readers = new Map();
  for (let i = 0; i < keys.length; i += BOUND) {
    const chunk = keys.slice(i, i + BOUND);
    const { results } = await db.prepare(`SELECT email, key FROM follows WHERE town = ?1 AND kind = 'address'
      AND confirmed_at IS NOT NULL AND key IN (${chunk.map((_, n) => `?${n + 2}`).join(", ")})`).bind(town, ...chunk).all();
    for (const { email, key } of results) {
      for (const mention of byKey.get(key)) readers.set(email, [...(readers.get(email) || []), { key, mention }]);
    }
  }
  const emails = [];
  for (const [email, items] of readers) {
    // Recorded before it's sent: a send tried again the same day skips whoever has it.
    const { meta } = await db.prepare("INSERT OR IGNORE INTO deliveries (email, town, day) VALUES (?1, ?2, ?3)")
      .bind(email, town, today).run();
    if (!meta.changes) continue;
    const token = await readerToken(env, email);
    const manageLink = `${url.origin}/alerts/manage/?m=${token}`;
    const unsubscribeLink = `${url.origin}${UNSUBSCRIBE_PATH}?m=${token}`;
    items.sort((a, b) => Date.parse(b.mention.posted) - Date.parse(a.mention.posted));
    emails.push(alertEmail(env, mentions.site || url.hostname, url.origin, email, items, manageLink, unsubscribeLink));
  }
  const { failed, inactive } = await sendEmails(env, emails, fetchImpl);
  // A reader the provider won't send to again (a bounce for good, a spam complaint): their alerts stop, and
  // everything about them is deleted.
  for (const email of inactive) await forget(db, email);
  if (inactive.length) console.error(`Alerts for ${town}: ${inactive.length} readers the provider won't send to were removed`);
  if (failed.length) {
    // Taken back, so the next publish today tries them again.
    for (const email of failed) {
      await db.prepare("DELETE FROM deliveries WHERE email = ?1 AND town = ?2 AND day = ?3").bind(email, town, today).run();
    }
    console.error(`Alerts for ${town}: ${failed.length} of ${emails.length} emails weren't accepted; tried again on the next publish`);
    return json({ ok: false, sent: emails.length - failed.length - inactive.length, failed: failed.length, removed: inactive.length }, 502);
  }
  await db.prepare("INSERT INTO sends (town, through, day) VALUES (?1, ?2, ?3) ON CONFLICT (town) DO UPDATE SET through = ?2, day = ?3")
    .bind(town, newest, today).run();
  return json({ ok: true, sent: emails.length - inactive.length, removed: inactive.length, mentions: fresh.length });
}

export async function route(request, env, url, manifest, ctx) {
  if (request.method !== "POST" || !enabled(env, manifest)) return null;
  switch (url.pathname) {
    case FOLLOW_PATH: return follow(request, env, url, manifest);
    case CONFIRM_PATH: return confirm(request, env, url);
    case MANAGE_PATH: return manage(request, env, url);
    case UNSUBSCRIBE_PATH: return unsubscribe(request, env, url);
    case SEND_PATH: return send(request, env, url, manifest);
    default: return null;
  }
}
