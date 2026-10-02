# Plan: acting on the October 2026 review

An outside review (2026-10-02) read both repositories and the live sites. Its
short version: the engine plans carefully for a thousand towns but doesn't yet
check what readers see today. This file turns its findings into work, in order,
with what "done" means for each. When a piece is finished it moves into
`ROADMAP.md` as that item's *Done* note, and its line here is struck out.

Findings marked *checked* were read in the code or measured; *inferred* ones
(mostly the 50- and 1,000-town numbers) are estimates to measure, not facts.
The code citations were re-read against `main` on 2026-10-02 and still hold.

## Decisions first

These are the maintainer's to make. Each one changes the work below, so they
come before anything is built.

| # | Decision | Recommendation | Changes |
|---|---|---|---|
| D1 | A decision that fails the new checks (A2): hidden, or shown marked "not checked against the minutes"? | Shown, marked, for the first month, so the failure rate is visible; then hidden. | A2 |
| D2 | 311 addresses already in git history: rewrite history once, or coarsen from now on only? | Coarsen from now on, and rewrite once when 311 data moves to R2 (C1), since that move rewrites the files anyway. | B3, C1 |
| D3 | Sponsorship: the SeeClickFix data is NonCommercial. | No sponsor until per-file licensing (B1) is live and the SeeClickFix terms are read for what "commercial" means for a sponsored nonprofit site. | B1 |
| D4 | Order of the next towns. | Lowell, then Spanish, then Lawrence (the review's order), with Lowell after the 311 work (C), since its 311 history is several times Malden's. | Stages |
| D5 | Spanish landing for Lawrence: always English, `Accept-Language`, or Spanish-first? | `Accept-Language` on the root only, remembered by the header switch; never on deeper links, so a shared link opens in the language it was shared in. | F4 |
| D6 | The $50 budget. | Keep $50; move backlog summaries to the Batches API (C5), which roughly doubles what $50 buys. Revisit at 15 towns with the ledger's numbers. | C5 |
| D7 | A second person with owner access to GitHub, Cloudflare, and Anthropic. | Name one before Lawrence. Not to do the work, so the sites survive if the maintainer is away. | B5 |

## A. A trust floor for what's live (about one week, first)

What readers see on every meeting page is the model's `decisions` list, and
nothing compares it to the minutes. The roadmap's rule for votes (item 12: a
claim shown only with the line it came from, checked without AI) is the right
rule; it should apply to decisions first, since decisions are already public.

**A1. Label every AI headline where it's shown.** *Checked.* The AI credit is
under the full summary only; the same headline is the preview on the homepage,
meeting lists, board pages, and the RSS feed (`write_feed`,
`pipeline/build_site.py:843`). Add a short "AI summary" marker to each preview
and "AI summary: " to the feed item's description. *Done when* a page check
fails any page that shows a summary headline without the marker.

**A2. Decisions anchored to the minutes' text.** *Checked.*
- The summary schema asks for each decision's `source_quote`: the line of the
  minutes it rests on, copied exactly.
- A check without AI: the quote, normalized for whitespace, hyphenation, and
  page headers, is in the document's text layer (already saved in the record);
  every number in the decision (money, tallies, dates) is in the quote; every
  name in the decision is in the quote and, for elected bodies, in the
  `[officials]` list.
- A decision that fails is handled as D1 decides, and counted in `run.json`.
- Scans: the AI transcription isn't evidence (item 12's rule), so a scan's
  decisions are always marked "from a scanned document, not checked".
- The "final" or "committee recommendation" sorting (`RECOMMENDED` and
  `PROCEDURAL`, `pipeline/build_site.py:206-224`) stays, but runs on the quote
  rather than the model's sentence where there is one.

*Done when* every decision on every live site is either checked or marked, and
the share checked is on the status page's maintainer view. The review's spot
check (378 tallies, all in the source) suggests most will pass; this makes it
something that's noticed when they don't.

**A3. An outcome that doesn't rest on one word.** *Checked:* 850 of 2,143 live
decisions have no tally, so only the verb says whether something passed, and
a dropped "not" can't be caught. Add an `outcome` field (`approved`,
`denied`, `tabled`, `referred`, `other`) to the schema, compare it with the
verb in the decision and the quote, and treat a disagreement as a failed check.

**A4. Prompt version bump and regeneration.** *Checked:* 250 of 401 live
summaries came from the previous prompt but carry the current version. Bump
the minutes and agenda kinds' `version` in `pipeline/summarize.py`; the 250
regenerate through the normal budget order (about $10 to $15). Add a test
that hashes each kind's prompt and schema and fails when the hash changes
without the version changing, so this can't happen again.

**A5. What the prompt doesn't ask.** Ask for the document's own meeting date
and whether it is marked draft or corrected. Show a mismatch with the calendar
date as a warning for the maintainer, never a silent fix; show "Draft minutes"
on the page when the document says so.

**A6. A real-model check, outside CI.** Tests only use a fake client. Keep
about 15 minutes and agendas (one per reader and town, including a scan and a
long Legistar document) with hand-written expected decisions, and a command
that runs the current prompt on them and reports differences. Run it on every
prompt change, by hand; it costs about a dollar.

**A7. Dead sources look dead.** *Checked.*
- SeeClickFix 403s (`pipeline/fetch_311.py:136`): a 403 for more than a few
  records in one run means we are blocked, not that the records were removed.
  Stop the step, keep the records, and report it. Mark a record removed only
  on 404 or 410, or on a 403 when other requests in the same run succeed.
- Calendars (`pipeline/fetch_meetings.py:362`): a calendar that listed
  meetings before and now lists none is a failed check, not a success.
- The 311 page's "Updated" date is the last successful 311 fetch, not the
  build.

**A8. Alerts after the first.** *Checked:* the daily issue is edited when a
second town falls behind, and edits send no email. Add a comment for each town
newly behind (comments email); keep editing the body as the summary.

## B. License, privacy, and keeping the lights on (about three days)

**B1. Per-file licensing.** *Checked.* No `LICENSE` in the network repository,
no license line on the sites. Write `LICENSE` with per-file terms: CC BY 4.0
for summaries, headlines, decisions, and compiled meeting data; CC BY-NC-SA 3.0
for everything derived from SeeClickFix (the 311 scorecard, the 311 CSVs, the
street lookup, and any search index that includes 311 text); public records and
public-domain figures as they are. A footer line on every page, and the About
page's credit line (item 15). Item 15's Data page then lists terms per file.

**B2. Keep the SeeClickFix terms.** Save the terms as read on the date the
data was taken, in the network repository, and note the date.

**B3. 311 privacy.** *Checked.* House-number addresses are public, forever,
for categories such as police non-emergency and homeless encampments.
- A list of sensitive categories per vendor, in the engine.
- For those, the address is cut to the block ("100 block of Main St") when
  saved, and the point to the block's middle. Removed records lose their
  address entirely.
- History as D2 decides.

**B4. Security quick wins.** *Checked*, in order of risk:
1. The steps that read untrusted PDFs and web pages run with the storage and
   sites keys in their environment (`pipeline/update.py:85` strips only the
   Anthropic and BLS keys), and checkout leaves the job's write-capable token
   in `.git/config`. Give each key only to the steps that need it (`secrets=`
   on each `Source`), the sites keys to the deploy step alone, and check out
   with `persist-credentials: false`, handing the token to the commit step
   only.
2. Pull request runs: build-only runs need no secrets; pass them to the town
   job's steps only when `github.event_name != 'pull_request'`, and give those
   runs `contents: read`.
3. The Worker sets `Strict-Transport-Security`, `X-Content-Type-Options`,
   `Referrer-Policy`, and a header `Content-Security-Policy` with
   `frame-ancestors 'none'` (a meta tag can't carry it).
4. Pin actions by commit SHA, pin `boto3` and `wrangler`, and turn on
   Dependabot for both repositories.
5. Branch protection on the engine's `main`, and tag protection for releases.
   Decide whether the moving `v1` tag is still needed now that the network
   pins `engine-version`; if not, stop moving it.

**B5. Bus factor.** A `RUNBOOK.md` in the network repository: where every
secret lives and how to rotate it, how to roll a town back, how to pause the
scheduler, what the token expiry on 2027-10-01 breaks. A second owner (D7).

## C. Before the next 311 town (about two weeks)

**C1. 311's raw store moves to R2 now.** *Checked, projection inferred.*
Malden's requests file is 10 MB, grows about 185 KB a day, and is rewritten on
every run; it reaches GitHub's 50 MB warning in about seven months, and Lowell
and Springfield are several times Malden's size. Item 1 is right but
scheduled for 20 to 50 towns; for 311 it's now. Raw requests go to the
`[storage]` bucket, by month, so a run rewrites only open months; git keeps
the derived scorecard and CSVs the site is built from.

**C2. One SeeClickFix job, paced across towns.** *Checked.* Pacing is per town
process, so parallel jobs multiply the rate against a vendor that has blocked
us. The plan job puts every town's 311 step in one job that runs towns in
turn under one pace, and the town jobs skip 311. That is item 2's per-vendor
pacing at the scale we have, without the work queue (item 9).

**C3. Nothing lost at a timeout.** *Checked.* A job of four 311 towns plus
summaries nears the 300-minute timeout, and on timeout the commit step is
skipped. Commit each town's data when that town finishes, give the commit step
`if: always()`, and give each town a time budget that ends well before the
job's. C2 removes most of the risk; this removes the rest.

**C4. Enforce the matrix's 256-job limit in `plan()`** with a test.

**C5. Batches for the backlog.** Upcoming agendas stay immediate; the backlog
and transcriptions go through the Batches API at about half the price. Replace
the equal share with a floor per town plus the rest by need, so a town with a
backlog doesn't starve the others' new documents.

## D. Smaller fixes (fit in between)

- **Accessibility.** *Checked.* The Accessibility page says every chart has a
  table; the 311 category and ward pages' charts don't. Add the tables (the
  claim is right as a rule). Every scrollable table gets `tabindex="0"`, a
  role, and a label, not only the About page's; a page check enforces both.
- **Officials going stale.** Each town's next election date in config; the
  status page flags a list not checked since (already item 11's plan; do it
  before Lowell, the first town with district seats added from scratch).
- **Config sprawl.** *Checked:* 116 of 215 config keys are used by exactly one
  town. Before writing another bespoke reader, list those keys by reader and
  fold the ones that are really reader defaults into the reader.
- **Use the page views already counted.** A monthly report from GoatCounter:
  which pages and towns are read. It should steer which features come next.

## E. Spanish, re-scoped (four to six weeks, after Lowell)

Item 14's approach stands: translate from the English summary, `/es/` on the
same host, a check without AI, a person before launch. What changes is its
size and a few gaps. *Checked:* there is no string layer, and English is
written in Python, JavaScript, 68 hard-coded URLs, and data files.

**E1. Data files say what, not how.** The 311 bucket labels in the scorecard,
the budget labels, and the sentence in New Hampshire's budget data are
English text written at fetch time. They become stable ids, with the words
added at build time. *Done when* no data file contains a sentence meant for a
reader.

**E2. One string layer, English unchanged.** Templates, Python format
functions (dates, plurals, durations, money), JavaScript strings, and URLs all
read from one file per language. *Done when* a town's English build is byte
for byte the same before and after.

**E3. Six AI fields, not one.** An agenda's headline, summary, and items, and
the minutes' headline, summary, and decisions, translated as one record keyed by the
English record's hash, with a `reviewed` flag. A regenerated English summary
doesn't silently replace a reviewed translation: the old one stays until the
new one is reviewed or the page falls back to English. The "final or
recommended" sorting runs on the English, never the Spanish.

**E4. The number check, built for English first.** It normalizes Spanish
forms (1.234,56 and 1,234.56; "10 a 1"; month names) or the prompt asks for
US forms; names and vote counts are checked as exact, as promised. Built first
as A2's English check, then reused.

**E5. What item 14 doesn't list yet.** Calendar meeting titles, a
network-wide glossary of board names, 311 categories, the About and state
pages, the share image, a second search index, `lang="en"` on English text
inside Spanish pages, and the Spanish 404 in the Worker. Landing as D5
decides.

**E6. Review that keeps going.** A Spanish reader checks the wording and a
sample of summaries before launch, then a small sample each month, with a
note of who checked what and when.

Lawrence's council minutes are posted late (5 for 2026 by October), so its
decisions page will be thin at launch in either language; say so on the page.

## What moves, what waits

- **Moves up:** a reader for Legistar or BoardDocs (each used by many towns)
  before any more one-town readers; the GoatCounter report.
- **Waits:** Maine, Vermont, and Rhode Island; statewide sources phase 3; the
  town helper. None of these is blocked, but each adds towns or sources faster
  than the trust floor can be kept.
- **Dropped as a design driver until 50 towns:** the thousand-town numbers.
  They stay in the roadmap as a direction; decisions are made on measured
  numbers at the size we are.

## Order

| Weeks | Work | Gate before moving on |
|---|---|---|
| 1 | Decisions D1 to D7; A1 to A4, A7, A8 | Every decision live is checked or marked |
| 2 | A5, A6; B1 to B5; D's accessibility fixes | `LICENSE` and footer live; secrets scoped; headers set |
| 3–4 | C1 to C5 | Malden's 311 off git; one SeeClickFix job; a timeout loses nothing |
| 5 | Lowell (config only), officials staleness | Lowell green for a week, decisions checked |
| 6–11 | E1 to E6 | English builds unchanged; a Spanish reader signs off |
| 12 | Lawrence, in both languages | |

After this, `ROADMAP.md`'s Stage 1 list is rewritten in this order, items 1
(for 311), 2 (pacing), and 4 (Batches) move from Stages 2 and 3 into Stage 1,
and item 14's plan takes in section E.
