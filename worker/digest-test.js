// Sends towns' newest weekly digest issues now, to test addresses alone (digest.js sendTest), so a
// change to the email is seen without waiting for Sunday. Run by hand, by the network's Digest test
// workflow:
//
//   node worker/digest-test.js gloucester-ma.publick.org     # the issue on the town's site now
//   node worker/digest-test.js _site/digest/feed.xml         # the issue in a site just built
//
// Environment: BUTTONDOWN_SEND_KEY; DIGEST_TEST_RECIPIENTS, the addresses, separated by commas or
// spaces. The addresses are never printed: the network's run logs are public.
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { pathToFileURL } from "node:url";

import { deleteTests, newestIssue, sendTest } from "./digest.js";

async function feedOf(source) {
  if (existsSync(source)) return readFile(source, "utf8");
  if (!/^[a-z0-9.-]+$/i.test(source)) throw new Error(`${source}: neither a feed file nor a site's hostname`);
  const response = await fetch(`https://${source}/digest/feed.xml`, { headers: { "User-Agent": "publick" } });
  if (!response.ok) throw new Error(`${source}: its feed: HTTP ${response.status}`);
  return response.text();
}

export async function main(sources, env = process.env) {
  const key = env.BUTTONDOWN_SEND_KEY;
  const recipients = (env.DIGEST_TEST_RECIPIENTS || "").split(/[\s,]+/).filter(Boolean);
  if (!sources.length) throw new Error("name a town's hostname, or a feed file, to send");
  if (!key) throw new Error("BUTTONDOWN_SEND_KEY isn't set");
  if (!recipients.length) throw new Error("DIGEST_TEST_RECIPIENTS isn't set");
  const deleted = await deleteTests(key);
  if (deleted) console.log(`Deleted ${deleted} earlier test draft${deleted === 1 ? "" : "s"}.`);
  for (const source of sources) {
    const issue = newestIssue(await feedOf(source));
    if (!issue) throw new Error(`${source}: no issue in its feed yet`);
    const subject = await sendTest(key, issue, recipients);
    console.log(`Sent "${subject}" to ${recipients.length} address${recipients.length === 1 ? "" : "es"}.`);
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2)).catch((error) => {
    console.error(`::error::${error.message}`);
    process.exitCode = 1;
  });
}
