# Roadmap

Last reorganized 2026-10-02, at six towns on engine v1.30.0; updated the same
evening after the meetings audit (engine #52, publick.org #40), and on
2026-10-03 after the AI summary label and the data license (engine #54,
publick.org #42), and that afternoon after every town moved to engine v1.33.0
by hand (publick.org #43) and the scheduler took over the morning's release;
on 2026-10-04, state legislators decided for later, and the audit of what
pages leave unexplained.

This file has four parts:

1. **[Where Publick is going](#where-publick-is-going)**: the vision, and the
   rules for choosing what comes next.
2. **[Priorities](#priorities)**: one ordered list, from this week to a
   thousand towns, with what's decided but not scheduled, ideas still to
   decide, and what isn't planned.
3. **[The work, by theme](#the-work-by-theme)**: each piece of work with why
   it matters, what's done, and what's next. The priority list points here.
4. **[Reference](#reference)**: how the network runs today, what's done by
   release, and where the old numbered items went.

## Where Publick is going

**What Publick is.** A free site for each town that turns what its government
publishes (agendas, minutes, budgets, tax rates, school figures, housing,
311 requests) into plain pages anyone can read: what's coming up, what was
decided, what it costs, and who decided it. Every summary links the document
it came from. Publick runs every town's site itself, from one network
repository and this engine.

**Who it's for.** Towns where local news has thinned out or left City Hall,
and the people in them who'd go to a meeting, write to a councillor, or just
want to know what happened, in the language they read first.

**Where it's going.**

- *This year:* a town in every New England state. Massachusetts, New
  Hampshire, and Connecticut have towns; Maine, Vermont, and Rhode Island are
  next. About 20 towns, each launched in English and Spanish.
- *Next:* most of the cities and larger towns of New England, chosen for the
  people they reach and how little local coverage they have, not only for
  how easy they are.
- *In the long run:* a thousand towns, run by very few people. That means
  adding a town in an hour rather than days, statewide sources fetched once
  per state, every check automatic, and a cost per town measured in cents a
  day.

**What a Publick site should be.**

- *Trustworthy.* Every fact traceable to a public document, every AI summary
  labeled and checked against its document, and a mistake easy to report
  and quick to fix. A wrong vote or amount next to a named person does real
  harm, so the sites show less rather than guess.
- *Complete.* Meetings, money, schools, housing, and services, plus who
  represents you and how they voted.
- *Current, and honest about it.* Each source on its own rhythm, and a page
  that says when its data is behind.
- *In the reader's language.* English and Spanish at every town; more
  languages where a town's people need them.
- *Open.* Free to read, no tracking cookies, and the data reusable under
  CC BY 4.0 with credit.
- *Light to run.* Static sites served from one Worker, one schedule, one
  budget, and a status page that tells the truth.

**How to choose what's next.** When two pieces of work compete, the one
higher on this list goes first:

1. **Nothing on a live site misleads a reader.** A wrong decision, amount,
   name, vote, or translation shown as fact; a security hole; a privacy line
   that isn't true. These are fixed before anything else.
2. **The sites keep running, and the status page tells the truth.** A
   failure that looks like success is worse than a failure.
3. **The process is safe before it's fast.** Fewer, checked releases;
   protected branches; secrets only where they're needed.
4. **More towns,** chosen for readers.
5. **New things for readers.**
6. **Work for scale when the numbers say so,** not before: each scale item
   says at how many towns it matters.

**Constraints today.** The network repository is public on GitHub's free
plan: Actions minutes are free, at most 20 jobs run at once, and a run's
matrix is at most 256 jobs. AI summaries and translations share a network
budget of **$50 a month**. One person runs and merges everything; about two
thirds of commits are written by AI.

## Where things stand (2026-10-02)

| Town | Live | Meetings from | 311 | Notes |
|---|---|---|---|---|
| Gloucester, MA | moved in | CivicPlus calendar (month by month) and Archive Center; School Committee in Google Drive, with its schedule | SeeClickFix | City permits file |
| Malden, MA | moved in | CivicPlus calendar and Agenda Center; School Committee dates from the district's page | SeeClickFix | Roll-call votes collected, not shown |
| Manchester, NH | moved in | CivicClerk and DotNetNuke | SeeClickFix | First New Hampshire town; NH figures saved by hand; the school board's site blocks automated reading |
| Beverly, MA | 2026-10-01 | CivicPlus calendar and Agenda Center; School Committee from the district's calendar feed | none | First town added from scratch; council minutes are scans |
| Wallingford, CT | 2026-10-01 | The town's own website; Board of Education on Finalsite, with its schedule | none | First Connecticut town; summaries from July 2026; worded as a town |
| Lawrence, MA | 2026-10-02 | CivicPlus calendar and Agenda Center | none | Launched in English and Spanish |

- Every town is in English and Spanish (`/es/`), engine v1.27.0 and later.
- Every town is on engine v1.33.0, published 2026-10-03 at 13:07 to 13:09 UTC,
  and passed "Check live site" on its first real run.
- 445 summaries live (agenda prompt v4: 62; minutes v2: 383).
- Summary and translation spending: September $21.82 (three towns); October
  $9.22 after two days, projected $50 to $60, so the cap binds this month.
- Network status: [publick.org/status/](https://publick.org/status/).
- 311 history in git: 24.5 MB across three towns, growing about 0.4 MB a day;
  Malden's and Manchester's backfills have about 20 and 14 days to go.

## Priorities

Each line points to its section under [The work, by theme](#the-work-by-theme).
Order within a group is the order to do them in.

### Now: this week

Done on 2026-10-02: the open redirect closed (Worker deployed), the Spanish
disclosed and checked without a person, releases and engine moves once a day
and by themselves, the status page right after a push, the rulesets on both
repositories, `ENGINE_PR_TOKEN`, $80 for October, Lawrence's catch-up run, and
summaries checked against their documents (engine v1.30.0 and pull request
#50), and the meetings audit's fixes, with a shorter home page (engine #52,
publick.org #40; see [Meetings, complete and correct](#meetings-complete-and-correct)).
Done on 2026-10-03: "AI summary" on every summary shown in a list (item 7) and
the data license (item 12), engine #54 and publick.org #42; items 4, 9, and 15;
and every town moved to engine v1.33.0 (publick.org #43), with the scheduler
deployed to start the release and the engine pull request each morning.
What's left:

1. **Watch the first scheduled release and engine move** (2026-10-04, 08:20
   and 08:40 UTC, started by the scheduler Worker). On 2026-10-03 neither
   started on GitHub's schedule by noon (its scheduled runs here start 5 to 7
   hours late), so the engine moved by hand, and the network's pull request
   check caught three problems before anything was published:
   - the engine workflow pushed with its read-only token, not
     `ENGINE_PR_TOKEN` (fixed: `persist-credentials: false`, publick.org #43);
   - v1.31.0's `network.py` needed `feedparser` in jobs that don't install it
     (fixed in #62, v1.32.0);
   - on v1.32.0 the browser checks opened moved meeting pages, which redirect
     themselves, and failed in Beverly, Lawrence, and Malden (fixed in #63,
     v1.33.0).
   Every town published on v1.33.0 at 13:09 UTC and passed "Check live
   site". Checked on the live sites: "AI summary" in the lists, the hearings
   box, and `/feed.xml`; "Reusing what's here" with its `LICENSE` link; the
   footer link; and the Spanish. Left: the 311 pages' "Updated" date shows
   the last fetch from the next daily run, whose scorecard records it.
   ([Releases](#releases))
2. **Check the first daily runs on the new engine**: every translation made
   again on prompt 3 and reviewed (about 450 summaries, about $4), how many
   fail the checks, and every summary's fact check recorded (60 documents a
   run, so about two days). ([Spanish quality](#spanish-quality),
   [Summary checks](#summary-checks))
3. **Check the first daily run with the meetings audit's fixes** (2026-10-03):
   in each town, `python -m pipeline.listings` lists the meetings shown as
   one, and a calendar meeting next to an Agenda Center one of the same day,
   not put together, needs an alias; the new boards' Spanish drafted; Gloucester's
   School Committee subcommittees listed once; Wallingford worded as a town;
   the Arts Commission correction shown on Manchester's November 9.
   ([Meetings, complete and correct](#meetings-complete-and-correct))
4. **Read two summaries by hand**: done 2026-10-03, both right. The Beverly
   City Council's "January 20, 2025, at 7:30 PM" is in the minutes as printed
   (page 3, Order #009), a typo of the city's the summary copied; the Golf and
   Tennis Commission's $8,000 and $3,100 are "NTE $8K" and "NTE $3.1K" in the
   scan. The check flagged the second because a written-out amount wasn't
   matched to the document's "$8K" (fixed the same day, fact check version 2).
   ([Summary checks](#summary-checks))
5. **The next meetings audit, by hand**, a week on: every town's upcoming
   meetings against its city's own sites, as on 2026-10-02.
   ([Meetings, complete and correct](#meetings-complete-and-correct))

### Next: October, before the next town

Make what's shown checkable, and the process safe, before adding towns.

6. **Summaries and decisions checked against their documents.** Every number,
   amount, date, and name in a summary is in the document's text; each
   decision anchored to a quote from the minutes, with an `outcome` field so
   a dropped "not" is caught. What fails isn't shown. The first half (numbers,
   amounts, dates, names) is done (#50); the quotes and `outcome` are built
   (2026-10-03, minutes prompt version 3) and passed item 8's run; they ship
   with the engine pull request after v1.34.0. ([Summary checks](#summary-checks))
7. **"AI summary" on every headline**: done 2026-10-03 (#54), in each list
    row's details line, on its own line in the public hearings box, and after
    the summary in the RSS feed. ([Summary checks](#summary-checks))
8. **A test set run against the real model**: done 2026-10-03, 15 minutes
    and 52 decisions checked by hand (`evals/minutes.json`,
    `python -m pipeline.evaluate`), run by the network's Evaluate workflow
    (publick.org #44). ([Summary checks](#summary-checks))
9. **Failures that look like success, fixed.** Done 2026-10-03: three
    SeeClickFix 403s in a row stop the 311 step instead of marking records
    removed (none had been wrongly marked: 0 of 41,718, checked 2026-10-02).
    Also done: the 311 pages are dated by the last fetch; a calendar that
    listed meetings and now lists none fails; the daily alert comments on
    each town newly behind (the network workflow's step goes in once the
    engine release with `behind --new-since` reaches `engine-version`).
    ([Monitoring](#monitoring))
10. **Security hardening.** No secrets on pull request runs,
    `persist-credentials: false`, actions pinned by SHA, Dependabot, HSTS
    from the Worker. Done: storage and sites keys only in the steps that use
    them (2026-10-03), boto3 pinned, tag protection, and a CSP on every page
    (a `<meta>` tag). ([Security and privacy](#security-and-privacy))
11. **311 addresses cut to the block** for sensitive categories (encampments,
    health reports), on the pages and in the CSVs: in a pull request
    (2026-10-03), from now on. Left: whether to rewrite what's already in
    git history, and `requests.json` itself, which keeps SeeClickFix's
    addresses until 311's raw requests move to R2 (item 20).
    ([Security and privacy](#security-and-privacy))
12. **The data license**: done 2026-10-03 (#54, publick.org #42), CC BY 4.0 in the network
    repository's `LICENSE`, a "Reusing what's here" section on every About
    page, and a footer link. Left: SeeClickFix's terms saved as read.
    ([Open data](#open-data))
13. **A budget floor per town**: in a pull request (2026-10-03). Every
    town keeps half its even share of each day's budget for every day left
    in the month; one town's run gets only what's left beyond the others'
    floors. ([AI costs](#ai-summary-and-translation-costs))
14. **Push runs build and check but don't publish**, so they never wait hours
    behind a daily run; the next daily run publishes. ([Releases](#releases))
15. **A check of each live site after publishing**: done 2026-10-03, the
    homepage, fetched through the Worker, is the build just published.
    ([Releases](#releases))
16. **A `RUNBOOK.md`** in the network repository, and **a second person with
    owner access** to GitHub, Cloudflare, and Anthropic.
    ([Security and privacy](#security-and-privacy))
17. **Every page says why something is missing.** Mostly done (#72 and the
    next): what's left is in the theme. First, the wrong
    statements: "Minutes not posted yet" on cancelled meetings and on boards
    whose minutes Publick doesn't collect, "A summary will be added" for one
    that never will be, "No agenda was posted" before Publick was collecting,
    and a calendar that couldn't be read shown as an empty week. Then one
    reason for every other gap, worked out from what the engine already
    knows, and a site check that fails a gap shown without one.
    ([Saying why something is missing](#saying-why-something-is-missing))

### Then: to about 20 towns

17. **Lowell**: config only. ([Next towns](#next-towns))
18. **Wallingford's figures**: the Connecticut package's tax bill and budget
    are built (2026-10-04); Wallingford's config takes them on the next
    release, then the Affordable Housing Appeals List. ([State packages](#state-packages))
19. **A town each in Maine, Vermont, and Rhode Island**, preferring config-only
    towns (Burlington, Lewiston, South Kingstown). ([Next towns](#next-towns))
20. **311's raw requests in R2, and one SeeClickFix job paced across towns**,
    before the next 311 town. ([Data out of git](#data-out-of-git),
    [Shared sources](#shared-sources-once-per-state))
21. **Springfield**, after Medford: CivicClerk with meetings sorted into
    boards. ([Next towns](#next-towns))
22. **The open data export**: each town's `/data/meetings.json` and a Data
    page. ([Open data](#open-data))
23. **The next readers**: Foxit full text (Manchester's minutes, the most
    common style not read free), and the next meeting platform a chosen town
    needs. ([Readers](#readers-for-more-platforms))
24. **Officials kept current**: each town's next election date, with the
    status page flagging a list not checked since. ([Who represents you](#who-represents-you))
25. **Vote records shown**, for Malden's council first, after a few weeks of
    a person checking every new vote. ([Vote records](#vote-records))
26. **Statewide sources, phase 2**: the Subsidized Housing Inventory and DESE,
    once for Massachusetts. ([Shared sources](#shared-sources-once-per-state))
27. **The helper for adding a town**, which also runs the first fetches and
    sets up email routing. ([Adding a town](#adding-a-town))
28. **Statewide sources, phase 3**: BLS and the Census, once for the country.
    ([Shared sources](#shared-sources-once-per-state))
29. **Spanish, the rest**: `/es/feed.xml`, a Spanish share image, decision
    labels matched by content rather than position, and Ward and District
    kept apart in Spanish. ([Spanish quality](#spanish-quality))
30. **Accessibility**: a table for every chart (every scrollable table
    reachable by keyboard: done 2026-10-03). ([Accessibility](#accessibility))
31. **Upkeep**: workflows' actions off Node 20; config keys only one town uses
    folded into their readers; a monthly page-view report.
    ([Upkeep](#upkeep))

### Stage 2: about 20 to 50 towns

32. Town data moved to R2, with git keeping config and code, when the run
    records' sizes say so. ([Data out of git](#data-out-of-git))
33. Canary towns (Manchester and Malden) on the newest release, and sampled
    checks when `engine-version` moves. ([Releases](#releases))
34. `CODEOWNERS` and required reviews, before the first editor from outside
    Publick. ([Who can change what](#who-can-change-what))
35. The scheduler Worker deployed when it changes, and reminders for the
    steps that stay by hand. ([Steps done by hand](#steps-done-by-hand))
36. Officials compared with each city's own pages, with differences opened
    as one issue for a person. ([Who represents you](#who-represents-you))
37. Summaries through the Batches API, at least for the backlog.
    ([AI costs](#ai-summary-and-translation-costs))

### Stage 3: about 100 to 1,000 towns

38. The work queue: sources due, per scope, with per-vendor rate limits, and
    a new town's history fetched on its own. ([Runner capacity](#runner-capacity-and-the-work-queue))
39. A paid GitHub plan or other workers, as the queue's length shows the need.
40. A summary budget sized to the network, with one priority order across
    towns. ([AI costs](#ai-summary-and-translation-costs))
41. A status page with search and filters, and a daily digest instead of an
    alert. ([Monitoring](#monitoring))
42. Self-hosted page counts, if GoatCounter's free use runs out.

### Decided, not scheduled

Kept so they aren't lost. Each comes into a stage when it's chosen.

- **A weekly digest for readers.** In this order, stopping when a step is
  enough: an `.ics` meetings calendar; a static `/digest/<week>/` page with
  its own feed (upcoming meetings, and decisions from minutes posted that
  week; no AI calls); then email sent from that feed by a provider such as
  Buttondown. Waits until the daily runs are stable and there's evidence
  people want email. Sections whose data is stale are skipped or flagged.
- **Links to meeting recordings** on each meeting's page, where the town
  posts one (YouTube for Beverly and Wallingford, 1623 Studios for
  Gloucester). Simple, and useful without any summary of the video.
- **Towns queued separately**, so runs for different towns don't block or
  replace each other. Only if replaced runs turn out to delay towns.
- **State legislators on the Officials page** (decided 2026-10-04: yes,
  after the municipal work). In an at-large town such as Wallingford, a
  state representative is the only official elected by a reader's own part
  of town, and state law (school aid, payments in lieu of taxes, housing
  appeals) is behind much of what the boards take up. Kept narrow: a "Your
  state legislators" block, labeled as the state's, with the House and
  Senate district map, each member's name, and a link to the legislature's
  own page for them; no votes or summaries of state business. Boundaries
  from the Census's state legislative district files and the members from
  each legislature, both fetched or kept once per state (see
  [Shared sources once per state](#shared-sources-once-per-state)), so it
  costs nothing more per town; the map reuses the ward map. Comes after
  explaining what a page is missing, roll call votes for at-large bodies,
  and collecting a town's data before it launches.
- **A town that runs its own site** gets its own repository calling
  `town.yml`, which keeps working for that.
- **The meetings audit stays by hand** (decided 2026-10-02): a person
  checks each town's upcoming meetings against the city's own sites, about
  weekly while towns are added. An automatic check would read the cities'
  sites the same way the pipeline does, and miss what it misses.
- **A correction can go up in English first** (decided 2026-10-02): its
  Spanish in `[strings.es]` is welcome but not needed; without it the Spanish
  page shows the English note until the town's next run drafts it, like the
  town's other text.

### Ideas to decide

New in this reorganization: proposals, not decisions. Each says what it
would take.

- **Follow an issue across meetings.** A project, an address, or a budget
  line comes up at several boards over months; a page per address or project
  that lists every meeting where it appears, from the documents' own text
  (addresses can be matched without AI). It's what readers who aren't at
  every meeting most lack.
- **How to take part, per board.** When and where it meets, how public
  comment works, how to get on the agenda, and the clerk's contact: a few
  lines of config per board, shown on its page.
- **An elections page.** Each town's next municipal election (needed anyway
  to keep officials current), the offices on the ballot, and links to the
  town clerk. No candidate content.
- **More languages, chosen by data.** The Census's language-spoken-at-home
  estimates say which towns have many households that speak another
  language and limited English: Portuguese (Framingham, Everett, Somerville),
  Haitian Creole (Brockton, Malden), Chinese (Malden, Quincy), Khmer
  (Lowell), Vietnamese. Each uses the Spanish pipeline as is: strings, a
  style guide, translated summaries, checks. A rule such as "turn a language
  on where at least 5% of households speak it with limited English" would
  decide it per town.
- **A public corrections log.** Every corrected summary, decision, or vote,
  with the date and what changed, on one page per town. Shows the
  report-an-error link leads somewhere.
- **For local newsrooms.** A weekly "what to watch" list (agendas with
  money, zoning, or contested items) for reporters in each town, and a page
  saying how to cite and reuse the data.
- **Compare with the neighbors.** Tax bill, budget per resident, school
  spending per pupil, and permits beside nearby towns, on each state's page.
  The figures are already fetched once per state.
- **Running costs, published.** What a town costs per month (summaries,
  storage, serving) on the status page, once costs are steady; useful for
  grants and for any sponsor.
- **Funding.** No sponsor until the data's license is per file (decided).
  Options to weigh after that: grants from journalism and civic-technology
  funders, local sponsors per town (clearly marked, no say in content),
  reader donations.

### Not planned

- **Summaries of meeting videos.** A recording has what minutes leave out
  (who said what, public comment) and comes weeks earlier, but automatic
  captions get names and numbers wrong, a long meeting costs several times
  its minutes, and a heated meeting is where an error does the most harm.
  Not planned unless that changes; linking recordings comes first.
- **Working around a website's browser check.** Some towns' sites stop
  automated reading of their meeting listings. The way in is asking the town
  to allow Publick's crawler, never getting around the check.
- **Votes from AI transcriptions.** A scan's transcription is never evidence
  for how a named person voted.
- **Scores, rankings, or "voted with" figures** for officials.
- **A change of the engine's license.** It stays MIT (moving to the AGPL was
  considered and dropped, 2026-10-02).

### Open decisions for the maintainer

From the October 2026 review, still to decide:

- 311 addresses already in git history: rewritten once, or coarsened from now
  on only.
- The order of the next towns (the lists under [Next towns](#next-towns) are
  a proposal).
- The budget after October ($80 for October 2026, decided 2026-10-02; $50
  otherwise): raised for good, or kept with backlog summaries through the
  Batches API.
- Who the second person with owner access is.

## The work, by theme

Each section: why it matters, what's done, what's next, and when it matters.

### Trust

#### Summary checks

**Why.** Summaries, headlines, and decisions (who moved and seconded, how
the vote went) are written by a model from agendas and minutes and published
without being checked against the document. A wrong amount, name, or vote
count reaches readers and, with open data, whoever reuses it. Of everything
not checked, it's the thing that can mislead a reader most. Each summary
links its source and the sites say summaries can be wrong, but nothing
catches the mistake.

**Done.** Prompt versions are tracked, and every live summary is on the
current version (agenda v4, minutes v2). The minutes prompt no longer has the
model retype the document. Summaries' decisions record who moved and
seconded.

*Done 2026-10-02 (pull request #50, released with the next morning's release; decided the same day):* `pipeline/factcheck.py`
checks every number, amount, date, name, vote count, and reference number in
a summary, its headline, and each decision or agenda item against the PDF's
own text, without AI, and saves the result in the summary's record. What the
site shows (decided): a decision, agenda item, headline, or sentence of the
summary with something not in the document's full text isn't shown, and the
page says how many decisions weren't; a vote count the document doesn't give
(counted from a roll call) is left out of the text, until vote records can
confirm it. Documents with scanned pages, and scans checked against the
model's transcription, hide nothing but those counts. `python -m
pipeline.factcheck` lists what it finds.

Measured on the 449 live summaries: every one checked against full text
passes; planted errors are caught 89 to 100% of the time; 103 of 1,131 vote
counts aren't in the minutes and come out. Two read by hand on 2026-10-03,
on scanned pages, were both right: a Beverly City Council decision dated
"January 20, 2025, at 7:30 PM" is what the minutes say (the city's typo for
2026), and a Beverly Golf and Tennis Commission summary's $8,000 and $3,100
are "$8K" and "$3.1K" in the scan. The check counted an amount written out in
full ("$8,000") as missing when the document abbreviates it ("$8K"), since
only the summary's own scale words were read. *Done 2026-10-03 (fact check
version 2):* an amount counts when the document gives exactly that amount
with a scale ("$8K", "$1.2 million"). Of the 81 saved scans' summaries, only
those two amounts change. No summary had a fact check saved yet, so the new
version re-checks nothing.

*Done 2026-10-03 (#54):* "AI summary" wherever a summary is shown in a list. In a
meeting row it goes in the grey line under the summary ("AI summary · Agenda
posted"), only on rows that show one; in the public hearings box, on its own
line under the summary; in the RSS feed, after the summary ("(AI summary of
the agenda. Check the original.)"). Never in front of the summary: a badge
on every row was tried and taken out as clutter. Spanish: "Resumen hecho con IA".

**Next.**
- *Done 2026-10-03 (#66), after the test set's runs against the real model:*
  each decision anchored to a quote from the minutes, checked without AI,
  with an outcome, so a dropped "not" is caught. The minutes prompt
  (version 3) asks for each decision's outcome (approved, denied, tabled,
  continued, referred, recommended, withdrawn, or other) and the minutes'
  own words for it; `decisions` stay sentences, and `decision_evidence`
  beside them has each one's outcome and quote, so nothing that shows or
  translates decisions changed. The check (fact check version 3): the quote
  is in the document, with spacing, punctuation, page headers, and line
  numbers ignored, and a quote cut with "..." found part by part; the
  outcome agrees with the decision's words and the quote's (a failed motion
  can't be approved, a count with fewer for than against can't be approved,
  a two-thirds vote that failed 9-5 is a denial); what fails isn't shown.
  The decision's numbers and names are also looked for near the quote
  (decided while building: listed, not held, since an item's heading can be
  pages before its vote; the run says how often a right decision is
  listed). Written-out counts ("In Favor: 2 In Opposition: 3") now confirm a
  vote count too. Checked offline on the 15 documents' real text with the
  quotes written down by hand: all 50 found, and every approved or denied
  decision turned round caught. Minutes of meetings since 2026-08-04 are
  made again (about 91 documents, about $2); older ones keep version 2's
  summary (`remake_since`; decided 2026-10-03).
- For a scan, the check runs against the model's transcription, which is
  weaker since both come from the model; a failure is listed, not held.
- *Done 2026-10-03:* each fetching run's record (`data/run.json`) counts
  the town's summaries by fact check result, how many the site holds
  something back from, the decisions and agenda items it leaves out, and the
  vote counts it takes out (`fact_checks`, from what `shown()` shows), to show
  how often the model gets one wrong. `python -m pipeline.factcheck` still
  lists each problem.
- The document's own date and any "draft" marking read from it.
- *Done 2026-10-03:* a test that fails when a prompt changes without its
  version (`tests/test_prompt_versions.py`): each versioned prompt's words and
  schema (agenda, minutes, transcription, translation, drafted text) pinned by
  a hash beside its version. A change passes once its version goes up, or,
  where what the old prompt made should stay on purpose (as when agendas
  gained a start time without a bump), once the new hash is pinned with a
  note saying why. The translation review's prompt has no version: a change
  there doesn't call for translating again.
- Every test, the translation tests too, uses the fake model client. *Built
  2026-10-03:* `evals/minutes.json`, 15 minutes from all six towns and 52
  decisions checked by hand against each document's own text (failed
  motions, two-thirds votes, a "not" that's part of what was decided,
  tabling, referral, withdrawal, numbered lines, a scan), and
  `python -m pipeline.evaluate`, which runs them through the real model and
  the checks, plants errors in what passes, and fails on a wrong outcome the
  site would show (about $1 to $2 a run). Run by hand from the network's
  Actions -> Evaluate (publick.org #44), on the network's key; first runs
  2026-10-03. The model gave all 52 decisions written down by hand the right
  outcome and wrote 199 decisions in all; every one of the 119 errors
  planted in what passed was caught. The runs found the check's own
  mistakes, all fixed before it shipped: a roll call quoted as the page
  shows it ("Yea: 3 - ...") where the PDF's text orders it otherwise; "if
  they fail" read as a failed motion; "deferred", "re-committed", and "held"
  in committee not known; "Withdrew" read as a name (the check on main too:
  ten verb forms the dictionary lacks); and Beverly's long scanned minutes
  cut off at 16,000 tokens (now 32,000). Checked again on the model's saved
  answers: none of the 199 held back. The five numbers listed as far from
  their quote were all on right decisions, so that check stays a note.
  Tuned on this set, so the run records' counts of what's held back are
  the measure on new minutes.

**Matters at:** now, before more towns and before open data.

#### Spanish quality

**Why.** Every town's site is in Spanish, translated by Haiku 4.5 and shown
before a person has read it. The second review (2026-10-02, v1.29.0) read all
77 saved translations beside their English and found meaning errors on live
pages that the check passed.

**What the review found, by harm.**
- *Live translations with meaning errors*, all passing the check and shown:
  "adjourned" as "se disolvió" ("dissolved itself", Beverly `05fa7a22`,
  `98a5657e`); "reappointment" as "reelección"/"reeligió" 7 times, and the
  non-word "renonombramientos" (`05842d82`); "Chair Houseman" (Scott D.
  Houseman) as "La presidenta Houseman", and other guessed genders ("la
  solicitante", "la Abogada Dole", "La directora"); "name the road after X"
  as "nombrar... después de"; "underage operative" as "un operativo menor de
  edad"; "all-alcoholic beverages license" lost "all-"; business signs as
  "señales" 19 times; "Enmiendó"; and Lawrence's own summary in Spanglish
  ("redeveloper el sitio en uso mixto commercial", `9c6ec1ee`). The summary
  translation prompt has no rule against guessing gender (`translate.py:60-70`;
  only the drafts prompt has one, `:274`).
- *The check passes wrong translations.* It compares digits, list lengths,
  and dates (`translate.py:149-168`). On crafted inputs, all of these pass:
  "voted not to approve" as "para aprobar"; Approved as Negó; "failed 3-4"
  as "aprobada 4-3"; Maria Rodriguez as Mario Rodrigues; Essex Street as
  Calle Elm; "$3 million" as "$3 mil millones"; "$1.2 million" as "$1.2 mil";
  7:00 pm as 7:00 a. m.; Tabled as Aprobó; unanimously as por mayoría;
  decisions reordered; invented extra numbers. Correct Spanish fails it:
  "$1.500", "$5,8 millones", "$2500" written "$2,500". Three of Beverly's
  four failures were correct translations.
- *A failed translation is never retried* (`current()` accepts a saved record
  whatever its check, `translate.py:96-101`), and the page says "aún no se ha
  traducido" (`meeting.html:118`), which isn't true.
- *Translations from an older prompt are still shown*: `shown()` ignores
  `prompt_version` (`translate.py:103-111`); Lawrence's two v1 translations.
- *Machine translation isn't disclosed.* The Spanish credit says only
  "Resumen escrito con inteligencia artificial (IA)" (`es.po`); the About
  page names only the summary model (`about/index.html:108`). The "(en
  inglés)" note on agenda and minutes links doesn't exist (`meeting.html:127`,
  `:160`).
- *The privacy line is false.* The Worker sets a one-year `lang` cookie
  (`worker/sites.js:134`); every About page says "No cookies"
  (`about/index.html:116`).
- *Machine drafts of a town's text are unlabeled and barely checked.* Nothing
  on the page marks a draft (`drafted` only feeds a build notice,
  `build_site.py:1291`). `check_text` (`translate.py:305-315`) passes "Mayor"
  as "Gobernador", "Conservation Commission" as "Comisión de Conversación",
  "Chairman" as "Presidenta". A batch of 80 is checked only by its length
  (`:349`), so a shifted batch puts every text on the wrong English.
- *A Spanish gap can freeze the English site.* With no budget left,
  `draft_texts` stops (`translate.py:328`) and the build exits 1 for any
  config text without Spanish (`build_site.py:1299-1306`), so a new tagline
  late in a month stops both languages from publishing.
- *Decision labels are matched by position.* A decision's kind (decided,
  recommended, procedural) is put on the Spanish by index
  (`build_site.py:422`); a reordered translation passes the check, so
  "Committee recommendations" can label a final decision.
- US dates pass as written ("10/17 y 11/8", Beverly `04ef6699`), and a
  Spanish reader takes 11/8 as 11 August. Write ambiguous dates out.
- Ward and District both become "Distrito {n}" (`es.po`), which garbles
  Beverly's School Committee (Wards 1-6 and Districts A and B).
- Gendered roles keyed by role, not person: Lawrence's "Vicepresidenta" and
  "Vicepresidenta del Concejo" (`lawrence.toml:313-314`) will be wrong for
  the next holder. Use the generic form.
- The feed and share image are English only (`build_site.py:1075`, `:986`),
  but Spanish pages link `/feed.xml` with a Spanish title (`base.html:37`).
- Lawrence's config still says its Spanish is checked "before the site
  launches".

*Done well:* what's shown is keyed by the hash of the English it came from and
checked again at display, so a stale translation can't sit beside newer
English; English fallback is marked `lang="en"`; only `/` negotiates
language, with `Vary` and a no-store 302; `hreflang` and `x-default` set; a
404 per language; board names keep the official English; Spanish search
covers the translations; translation is cheap.

**Decided 2026-10-02: no person checks the Spanish.** A human reviewer isn't
realistic, so the sites say plainly that the Spanish is machine-translated,
always link the English, and two checks stand in for a person, failing to
English rather than showing a doubtful translation.

*Done 2026-10-02 (engine v1.30.0):*
- Disclosure: a translated summary's credit says "Traducido automáticamente
  con IA del resumen en inglés; puede tener errores" and links the English;
  every Spanish page's footer says the page was translated with AI and links
  the English; agenda and minutes links say "(en inglés)"; the About page
  names the translation model and both checks; the cookie line says the
  language switch sets one cookie and nothing else (decided: reword, keep the
  cookie).
- A config text with no Spanish shows in English with a warning instead of
  stopping the build (decided: reverses the morning's rule that a town isn't
  built in Spanish until all its text is), and drafting may spend $0.05 a run
  past the budget so the gap fills the next run.
- The check without AI, entry by entry: numbers kept and none added,
  whatever the Spanish number format; amounts' million or billion; a.m. and
  p.m.; names kept as written (a capitalized word that isn't an English word);
  and outcomes not turned round (a "not" lost or added, denied as approved,
  tabled as approved, unanimous changed). Every crafted case above fails it;
  76 of the 77 saved live translations pass.
- Claude Sonnet 5.5 reviews each translation that passes, and each batch of
  drafted town text, for meaning (adjourned as dissolved, guessed gender,
  "Comisión de Conversación"): about a cent each. Anything it flags shows
  the English.
- A failed translation or draft is made again once, then left; the page says
  the translation didn't pass our checks. Older prompt versions aren't shown.
- The prompt (version 3): never guess gender (use the English's, else the
  name and then the role); write dates with the month's name; a glossary for
  adjourn, appoint and reappoint, table, sign, name after, all-alcoholic
  license, underage operative.
- Lawrence's config: generic Vicepresidente for roles whose holder changes.

**Still to do.**
- Decision labels matched by content rather than position (a reordered list
  now fails the check entry by entry, so this is belt and braces).
- Ward and District both "Distrito" in Spanish; Beverly's districts are
  lettered, so they read apart, but a town with numbered districts would not.
- `/es/feed.xml` and a Spanish share image.
- A "report a translation error" path that reaches the glossary: today's
  report link goes to the same place as every other error.

**Matters at:** now.

#### Vote records

**Why.** How each member voted, motion by motion, from the minutes. It's the
most direct record of what elected officials do, and the most sensitive
thing the sites would publish: a wrong vote attributed to a named person
does real harm, and is read as taking sides. So a vote is shown only as the
minutes record it, by name, with the exact line it came from, and nothing is
inferred.

**What the minutes give.** The evidence for a vote is the PDF's own text
layer, never an AI transcription. How minutes record votes differs by city:
- Malden's City Council lists roll calls in a fixed form ("Yea: 10 - <ten
  surnames> Nay: 1 - <one surname>"), readable without AI. Every Malden
  council and Committee of the Whole minutes from January to September 2026
  (21, all with a text layer): 128 roll calls, every group's count matching
  its names. A parser has to handle a member present but not named (shown as
  "not recorded in this vote", never absent or yes); votes as sentences
  ("Councillors <three surnames> dissenting", "voted present"); a stated
  tally that disagrees with the names (left for a person); page headers
  inside a roll call; and a "minutes" link that serves the agenda.
- Manchester's Board of Aldermen names members in sentences ("Aldermen
  <three surnames> voted yea").
- Wallingford's Town Council minutes are a "Record of Votes and Minutes": each
  roll call lists every member on a line of their own (`TATTA: NAY`,
  `ROSSACCI: ABSENT`) with the tallies. The clearest form yet.
- Gloucester's minutes mostly give counts, with names only for some roll
  calls.
- Many votes are voice votes or "unanimous", with only who was present.
- Beverly's council minutes are scans: no text to read a vote from.

**Done (collected, not shown).** `pipeline/votes.py` reads roll calls from
minutes in Legistar's style (laid out by `pipeline/pdftext.py`), for every
body in a town's `[officials]` table, with no AI. Each roll call keeps its
item number, motion and outcome as worded, any note after the outcome, and
the exact lines of its groups. Checked: each group's count matches its
names, each name is exactly one member, no member is named twice, and a
stated tally matches the groups. Votes are kept in the minutes' record and
read again when the rules or the members change. `python -m pipeline.votes`
lists every roll call for a person to check. Malden 2026: 128 roll calls,
127 checked.

**Next** (on hold since 2026-10-01; votes are collected daily and shown
nowhere).
- A few weeks of a person checking every new vote, then a per-town switch
  that puts checked votes on meeting pages.
- A vote's item number: where items run together, a vote can take the
  previous item's number. Fix before votes are shown.
- Past members: the member list is today's, so a vote from before a seat
  changed stays unchecked. Showing older votes needs who served when.
- A member the minutes name differently (a changed surname) needs an alias
  on the member, never a guess.
- Readers for other styles: Wallingford's (one line per member), then
  Manchester's sentences, then Gloucester's named roll calls. For other
  formats, the summary step extracts each named vote with the line it came
  from, kept only if a check that isn't AI confirms it (the line is in the
  PDF's text, every name matches the member list, the counts add up).
- A unanimous or voice vote shown as that, with the members present, never
  as each member voting yes.
- Pages: each motion's vote on its meeting page, each member's votes on
  their officials entry, every vote linked to its minutes, each with the
  report-an-error link.
- On the page: votes appear only once minutes are posted, often weeks after
  the meeting, and only as far back as the town's saved minutes go.

**Matters at:** after the summary checks, when summaries have been reliable
for a while.

### Running the network

#### Monitoring

**Why.** With every town in one workflow, the run needs to say which towns
are behind and why, without flooding the inbox, and to say so truthfully.

**Done.**
- A town's failure doesn't fail a daily run. Each fetching run writes the
  town's `data/run.json`: every source's last update, the engine version,
  build and deploy result, and when the town last had a good update. A run
  that only builds, as for a pull request, still fails, so a broken site
  can't be merged.
- The network status page, [publick.org/status/](https://publick.org/status/),
  built by `scripts/build_status.py` after each run. It's public, so it says
  in plain words which data may be out of date and leaves internals to the
  run's summary.
- One alert a day: `pipeline.network behind` lists towns more than about 30
  hours without a good update and towns whose figure checks keep failing;
  the daily run opens, updates, or closes one issue, assigned to the owner.
- The scheduler Worker (`worker/scheduler.js`) opens, and later closes, a
  "network stopped" issue when no daily run has finished for 30 hours.

**Found 2026-10-02, and fixed the same day (v1.30.0).** The status
page and the alert were wrong for most of a day. The 09:06Z daily run failed "Check site" for Gloucester, Malden, and
Manchester (`/311/` scrolled sideways by 1px on a phone once charts had a
25th month). v1.28.0 fixed it and push runs republished all three, but a
push run doesn't write `run.json`, so they stayed `deployed: false`, the
status page said "Some data delayed", and issue #34 stayed open until the
next fetching run. The daily run finished green with 3 of 3 towns not
deployed; the only sign was warnings.

*Built:* a run that publishes a town without fetching records it in the
town's run record (that run's build, checks, and publish replace the failed
ones), commits it, and rebuilds the status page and the daily issue; a
fetching run's report names the towns it didn't publish, and fails when it
published none.

**Next.**
- *Done 2026-10-03:* a SeeClickFix 403 no longer marks a request removed on
  its own. A 403 followed by a lookup that works is a request made private,
  marked removed as before; three in a row (`REFUSED_IN_A_ROW`) is a block:
  the lookups stop, none of the three is marked removed, what was fetched is
  saved, and the step fails, so the run reports 311 behind.
- *Done 2026-10-03:* the 311 pages say "Updated" with when SeeClickFix was
  last read (`fetched_at`, from `311/status.json`), not when the scorecard
  was worked out again, so a day the fetch failed doesn't read as updated.
- *Done 2026-10-03:* a calendar that listed meetings and now lists none
  counts as failed (decided: every run until it lists meetings again, so a
  person looks; none had ever listed zero). The meetings recorded stay as
  they were, and the town shows as behind after about two days.
- *Done 2026-10-03:* `python -m pipeline.network behind --new-since FILE`
  prints a comment naming the towns not in the issue's earlier text
  (decided: only towns newly behind; one already listed or caught up says
  nothing). The network workflow saves the issue's text, edits it, and
  posts that comment, since an edit sends no email and a comment does.
- The status page flags an officials list not checked since the town's last
  election.
- At hundreds of towns: search and filters on the status page, and a daily
  digest instead of an alert.

**Matters at:** now.

#### Scheduling

**Why.** GitHub starts scheduled workflows when it can: on 2026-09-29 the
runs due at 5:17 to 8:17 a.m. Eastern started between 11:47 a.m. and 2:05
p.m. And in one concurrency group only one run can wait; a newer one
replaces it, silently.

**Done.**
- Runs don't lose data when two update the same town (v1.7.1): town jobs
  check out the latest `main` when they start, and `commit-data.sh` keeps
  both runs' changes after a collision.
- Towns picked by need: a daily run takes the towns whose last fetching run
  finished more than 18 hours ago, oldest first (`pipeline.network plan
  --due-hours`), so a late or doubled start does nothing more and a missed
  one is made up by the next.
- Cloudflare starts the runs: the `publick-scheduler` Worker (a Cron
  Trigger) starts a daily run every hour from 09:05 to 14:05 UTC through
  GitHub's API, with a fine-grained token (`SCHEDULER_GITHUB_TOKEN`, expires
  about 2027-10-01; a reminder is set for 2027-09-17).

**Next.**
- One GitHub backup schedule, not four (see [Releases](#releases)).
- Renew the scheduler token before 2027-10-01. When it lapses, runs fall
  back to GitHub's schedule and the "network stopped" check can't open its
  issue.
- Towns queued separately, only if replaced runs turn out to delay towns.

**Matters at:** now, and done enough for Stage 1.

#### Releases

**Why.** The network runs every town on one engine version, so a bad release
breaks every site that uses the broken part. And the way code moves costs
more than the checks do: the engine released 22 times in four days and
`engine-version` moved 17 times, 8 on 2026-10-01 alone (7 releases in about
10 hours on 2026-10-02). Each change is two pull requests, and every move
checks every page of every town and republishes them all. At that pace,
canary towns can't work: every release reaches every town within the hour.

Also from the process review (2026-10-02):
- A push to `main` waits up to 320 minutes behind a daily run, holding a
  runner (`network.yml`); on 2026-10-01 three push runs were cancelled after
  waiting 1, 1, and 5 hours.
- Ten starts a day (the scheduler's six and four GitHub backups), and 11 of
  91 network commits are "Update statewide sources", written even when
  nothing was fetched.
- The accessibility tests check every page in dark mode too
  (`tests/test_accessibility.py`), doubling the browser runs, though every
  page sets `color-scheme: light` and has no dark styles.
- Nothing looks at the live sites after `deploy publish`.
- One person merges their own pull requests, neither repository protects
  `main`, and a merge releases to every town. The tests are the only check.

**Done.** Automatic releases: when the engine's tests pass on `main`, that
commit is released, and a pull request's label picks patch, major, or none.
The engine's tests build Gloucester (CivicPlus calendar), a New Hampshire
site, and two sample towns (`tests/test_sample_towns.py`): Manchester
(CivicClerk and DotNetNuke) and Malden (Agenda Center), through the real
fetchers from saved pages, each with the page, link, and browser checks.

*Done 2026-10-02 (engine v1.30.0, publick.org #38):*
- The engine releases once a day (08:20 UTC), not on every merge: the newest
  tested commit on `main`, with everything merged since, at the largest bump
  its pull requests' labels ask for. **Actions → Release → Run workflow**
  releases now, for an urgent fix. A Markdown-only pull request is left out
  without a label.
- The network moves `engine-version` once a day by itself (publick.org's
  `engine.yml`, 08:40 UTC, decided 2026-10-02): it opens a pull request, waits
  for its run to check every page of every town, and merges it if every town
  passes; the merge publishes them. If one fails, nothing moves and the
  workflow's failure emails the owner. The network doesn't follow the `v1`
  tag: the pinned version is what rolls back, and its pull request is what
  checks a release.
- Rulesets (decided 2026-10-02, set the same day): the engine's `main`
  requires a pull request and the `test` check, and its `v*.*.*` tags can't be
  changed or deleted; the network's `main` blocks force-pushes and deletion.
  Engine pull requests are merged by a person: the one human look before code
  reaches every town.
- Accessibility tests in light only, until the sites have dark styles.
- One GitHub backup schedule; the statewide status written only when a
  source was fetched or something changed.

**Next.**
- A push builds and checks the towns it touched but doesn't publish them, so
  it doesn't wait; the next daily run publishes (chosen 2026-10-02). If
  that's too slow, towns queued separately lets a push wait only for its
  own towns.
- *Done 2026-10-03:* the scheduler Worker starts the engine's release at
  08:20 UTC and the network's engine pull request at 08:40, as it starts the
  daily runs, since GitHub's own schedule started neither on the first
  morning (`worker/scheduler.js`, `RELEASE_CRON`, `ENGINE_CRON`). The
  workflows keep their schedules as a late backup. On since 2026-10-03:
  `SCHEDULER_GITHUB_TOKEN` widened to the engine repository (Actions read
  and write), the two crons and `ENGINE_REPOSITORY` in
  `wrangler.scheduler.toml` (publick.org #43), and the Worker deployed at
  13:50 UTC with triggers at 08:20, 08:40, and 09:05 to 14:05.
- *Done 2026-10-03:* after publishing, a "Check live site" step fetches each
  town's homepage through the Worker until it is the built `index.html`,
  byte for byte, for up to two minutes (a Worker reuses a manifest for one).
  A mismatch fails the town, and it doesn't count as published in its run
  record (`pipeline.deploy check`). The page itself is compared because
  Cloudflare drops the Worker's ETag from HTML (checked on the live sites).
- Sample towns still to add: one with SeeClickFix departments, one on the
  town-website reader (Wallingford), one on Finalsite.
- At 20 to 50 towns: canary towns, Manchester and Malden (chosen
  2026-10-02), take each release a day ahead; `engine-version` moves for the
  rest after a day with the canaries green, by a one-line pull request a bot
  can open, which checks every page of the canaries and a sample of the
  rest. Rolling back is moving `engine-version` back; no town is kept a
  release behind.

**Matters at:** now; canaries and sampled checks at about 50 towns.

#### Security and privacy

**Why.** One Worker serves every site, one set of secrets serves every town,
and the 311 data has people's house numbers.

**Next** (from the October review, rechecked on v1.29.0).
- *The open redirect* (now): `worker/sites.js:135`, checked live.
- *Secrets only where needed:* pull request runs get every secret
  (`network.yml:371-381`); no `persist-credentials: false`. *Done
  2026-10-03:* each of a town's steps gets only the keys it uses. In
  `pipeline/update.py`, the document bucket's keys go to the six steps that
  save or read agendas and minutes (`STORAGE`), and no step gets the sites
  bucket's; in `pipeline/network.py` (`keyed`), the fetch gets the fetching
  keys, Publish the sites keys, and the freshness check, build, and checks
  none; `town.yml` the same, step by step. So a pull request run's steps get
  no keys, whatever the workflow passes. The steps that parse PDFs still
  hold the document keys, since they read the PDFs from the bucket: keeping
  them apart would take a download step of its own.
- *The Worker:* no HSTS (`sites.js:94-100`, checked live); add security
  headers. Every page already sets a Content Security Policy in a `<meta>`
  tag (`base.html`: scripts from the site only, no inline scripts); what a
  `<meta>` policy can't set, `frame-ancestors`, would come from the Worker.
- *Supply chain:* actions pinned by tag, not SHA; boto3 unpinned; Dependabot;
  `v1` moved on every release; tag protection.
- *Privacy:* 26,878 of 41,718 311 records carry a house number, among them
  389 Manchester "Homeless Encampment" and 273 Gloucester Health Department
  reports, and the CSVs export locations (`build_site.py:1243-1246`). *In a
  pull request (2026-10-03):* a sensitive category's address is shown to its
  block ("200–299 Main St") on every page, map, CSV, and in the street
  lookup, and its map point to three decimal places (about 100 meters):
  encampments, health and police complaints, noise, problem and private
  property, smoke detector, lost pet, and lead service requests
  (`seeclickfix.SENSITIVE_CATEGORIES`, plus a town's `[seeclickfix]
  sensitive_categories`). The requests stay listed and mapped, and still
  link to SeeClickFix, which shows the full address. Left: whether to
  rewrite git history once; `requests.json` and older `scorecard.json`
  commits keep full addresses in the public repository.
- *The privacy line:* the `lang` cookie against "No cookies" (see
  [Spanish quality](#spanish-quality)).
- *Continuity:* a `RUNBOOK.md` in the network repository (secrets, rotation,
  rollback, what to do when a source refuses), and a second person with
  owner access to GitHub, Cloudflare, and Anthropic.

**Matters at:** now.

#### AI summary and translation costs

**Why.** A per-town limit grew with the number of towns ($5,000 a day at a
thousand). The network has $50 a month for summaries and translations, and
one Anthropic key whose rate limits apply to the whole network.

**Done.**
- The ledger: each town's `data/summary-costs.json`, each month's cost and
  documents, recounted from the saved summaries (which record their cost),
  plus what cut-off responses cost.
- The network budget: the plan job adds up the month across towns and gives
  each town in the run an equal share of what's left (`pipeline.network
  budget`). A town stops at its share, or its per-run limit ($5 and 50
  documents), whichever comes first. Translations count in it
  (`network.py:400`).
- The priority order, within each town: upcoming agendas, then documents
  fetched in the last two weeks for a meeting in the last two months, then
  the backlog, which may use what's left beyond a fifth of the budget,
  spread over the month and every town.
- Summaries no longer have the model retype the document (about 60% of the
  cost). A document's full text is laid out free from its PDF for a style
  the engine supports (`pipeline/pdftext.py`, Legistar's to start); a scan
  is transcribed by the model after every summary waiting, from what's
  left; any other PDF has a text layer and the page links it.
- Documents of up to 100 pages are summarized (60 before).
- Costs are the maintainer's: never on public pages, left out of exports.

**Spend.** Summaries cost about 2 to 12 cents each; translations about $0.003.
October after two days: $9.22 (Beverly $5.37, 58%, its launch backlog and
$1.92 of transcripts; Wallingford $1.33; Manchester $1.26; Malden $0.92;
Lawrence $0.21; Gloucester $0.13). Projected $50 to $60, so the cap binds.

**Next.**
- A one-time `catch_up` run for Lawrence (launched with a $0.17-a-run backlog
  allowance).
- *Done 2026-10-03:* `[summaries] since` summarizes only meetings on or after
  a date; older ones keep their records and documents, without summaries.
  Decided: a new town summarizes from about three months before it launches
  (`ADDING-A-TOWN.md`), since a launch's history is what took most of the
  money (Beverly, 58% of October's first two days). The live towns keep
  theirs (94 documents left, about $8.50).
- *In a pull request (2026-10-03):* a floor per town. Each town keeps `TOWN_FLOOR_SHARE`
  (half) of its even share of each day's budget for every day left in the
  month; a run's towns get what's left beyond the others' floors, so one
  town's launch backlog or busy week can't take the month. The budget step
  prints each day's `floor` and what's `kept_for_floors`.
- More free full-text styles, each added once for every town on the same
  software: Foxit (Manchester's) next by count.
- The Batches API, at least for backlog summaries: cheaper, and out of the
  daily run's way.
- At a hundred towns: one priority order across the network (upcoming
  agendas everywhere first), and a budget sized to the network.

**Matters at:** now.

#### Steps done by hand

**Why.** Fine at a handful of towns, not at fifty:
- Deploying the Workers (`worker.yml`, by hand).
- New Hampshire's yearly figures, downloaded in a browser because the
  state's websites refuse automated requests.
- Officials, edited after every election.
- A new town's first fetches, run by hand until nothing is waiting.
- An email routing rule for each town, in Cloudflare's dashboard.

**Next.**
- The scheduler Worker deploys itself when `engine-version` or
  `wrangler.scheduler.toml` changes on `main`. The sites Worker stays by
  hand: its routes decide which hostnames it answers, so a mistake takes
  sites down.
- The helper for adding a town runs the first fetches and sets up email
  routing through Cloudflare's API.
- Officials and New Hampshire's figures stay by hand, with a reminder after
  each town's elections and each year.

**Matters at:** about 20 towns for the helper; about 50 for the rest.

#### Upkeep

- Move the workflows' actions off Node 20, which GitHub has deprecated.
- Config keys used by one town: 143 of 277, plus 320 of 325 `[strings.es]`
  keys. Fold one-town keys into their readers where the reader can know them.
- A monthly page-view report from GoatCounter (the one `publick` site, each
  town under its folder name as a prefix).
- Renew `SCHEDULER_GITHUB_TOKEN` before about 2027-10-01.

### Growth

#### Adding a town

**Why.** The first three towns were moved in from repositories of their own,
whose configs were written by hand over weeks. From here each town starts
from nothing: which meeting system its city uses, its DLS name and code and
school district, its SeeClickFix organization and ward map, its BLS series,
its colors, its officials, and its Spanish text. Hours a town by hand.

**Done.**
- The checklist, `ADDING-A-TOWN.md` in the network repository.
- A town's update skips every step whose config table it doesn't have (most
  towns have no 311, permits file, or School Committee in Drive).
- Agenda Center categories written last-name-first ("Health, Board of") are
  turned round in the engine. Minutes are tied to `[meetings]`, not
  `[archive]`, so Agenda Center and CivicClerk towns get minutes without an
  Archive Center (since v1.17.1).
- Every lookup the helper needs answered Beverly's from a public API: the
  DOR code from `states/ma/`, the DESE district from the state's education
  data portal, the BLS area (and so the Census place) from BLS's area list,
  and the ward and precinct file from MassGIS. Beverly went live 2026-10-01,
  Wallingford the same day, Lawrence on 2026-10-02.
- A town's Spanish text needs no one's translation to launch (v1.29.0): its
  own `[strings.es]` if it has it, else the engine's Spanish for what many
  towns share (`pipeline/common_strings.py`), else a machine draft checked
  without AI and reviewed in batches (`python -m pipeline.translate drafts`).

**Next.**
- The helper: finds what it can for a town and state (DLS name and code,
  DESE district, BLS series, Census place or MCD code, whether the site is
  CivicPlus with a calendar or an Agenda Center, the SeeClickFix
  organization), writes a starting config with the rest marked to fill in,
  runs the first fetches until nothing is waiting, and sets up the email
  routing rule.
- SeeClickFix's issue listing (`/api/v2/issues`) now refuses requests
  without a login, so the helper can't measure a city's 311 activity from
  it. The Open311 listing the engine reads still answers; each town's
  organization ID is found by hand.
- A town whose meeting system the engine doesn't read is found the same way,
  and that reader becomes its own piece of work.

**What researching towns has shown** about what new towns need:
- Some are config only: an Agenda Center or CivicClerk city, often with
  SeeClickFix, whose School Committee posts in the same place.
- Some cities' websites are on platforms the engine doesn't read yet (a
  Govstack document manager, an older Drupal-based CivicPlus site). Each is
  a reader of about two days, written once for every town on it.
- Some councils post minutes as scans, which the model transcribes, at a cost
  every month.
- Some websites check each visitor's browser, which stops automated reading
  of the meeting listings (the PDFs themselves download). The way in is
  asking the town to allow Publick's crawler.
- School districts often post on their own websites (ParentSquare, Campus
  Suite with Google Drive files, Finalsite, BoardDocs, Diligent Community):
  each platform a small reader, used by every district on it.
- Small New Hampshire towns need building permits found by the Census's
  town (MCD) code (done, `[housing] bps_mcd`, for Wallingford) and school
  districts without a high school, so without a graduation rate.

**Matters at:** the next town; the helper at about 20 towns.

#### Next towns

Chosen for the people they reach and how little local coverage they have,
not only for ease. A proposal: the order is an open decision.

**Massachusetts** (from a survey of about 65 city and town websites,
2026-10-01; Salem and Medford were already in the works).
1. **Lawrence** (89,000). *Live 2026-10-02*, in English and Spanish. Its
   council posts minutes late (5 for 2026 by October), so its decisions are
   thin; the page should say so.
2. **Lowell** (115,000, the fourth-largest city). Config only: the City
   Council (30 agendas and 28 minutes in 2026) and School Committee (23 and
   20) in one Agenda Center, minutes with a text layer. Councillors elected
   by district, which the Officials page's wards fit. A large Cambodian
   community (see the languages idea under [Ideas to decide](#ideas-to-decide)).
3. **Springfield** (155,000, the third-largest). CivicClerk, as Manchester:
   428 events from January to September 2026, most filed under "General", so
   meetings have to be sorted into boards. Medford is on CivicClerk too, so
   Springfield comes after it. Nearly half its residents are Hispanic.

After those:
- *Config only:* Methuen, Chicopee, Waltham, and Fitchburg (Agenda Centers
  with council minutes as text), and a North Shore group beside Beverly and
  Salem (Swampscott, Saugus, Danvers), governed by a Select Board and Town
  Meeting.
- *Agenda Centers with council minutes as scans* (a transcription cost every
  month): Taunton, Leominster, Westfield, Pittsfield, Weymouth.
- *CivicClerk:* Watertown, Bridgewater.
- *Browser checks or refusals:* Newton, Arlington, Chelsea, New Bedford,
  Newburyport, Marblehead, Barnstable; possibly Woburn and Peabody.
- *Platforms the engine doesn't read yet:* Lynn, Brockton, Quincy, Fall
  River, Somerville, Worcester, Cambridge, Haverhill, Revere, Holyoke,
  Everett. Framingham and Melrose have Agenda Centers that load only with
  JavaScript.

**Maine, Vermont, Rhode Island** (researched 2026-10-01, none chosen):
- *Vermont: Burlington* (45,000). CivicClerk with text minutes, and
  SeeClickFix the city answers: config only. The Free Press no longer covers
  City Hall. Needs its current ward file found; the school board is on
  Diligent Community, a new reader. Montpelier and Winooski (8,000 each,
  CivicPlus) are config only too, but have no 311 and may be too small for
  BLS figures.
- *Maine: Lewiston* (37,000). CivicPlus as Gloucester's, text minutes, the
  School Committee in Google Drive: config only, schools included; a
  contentious council; no 311. Portland (68,000) is config only too
  (CivicClerk, SeeClickFix), with a bigger audience, but 22 boards and its
  311 volume weigh on the summary budget, and its school board is on
  BoardDocs.
- *Rhode Island: South Kingstown* (32,000). CivicClerk and SeeClickFix the
  town answers: config only; URI's college town, with an overflowing
  school-cuts meeting in 2026. Pawtucket (76,000) has the bigger audience
  and needs a reader for the Secretary of State's Open Meetings portal,
  where every Rhode Island public body posts agendas and must file minutes
  (text PDFs in every sample): one reader of about two days for all 39
  municipalities. Providence is too big for now.

Every New England state now has a package (2026-10-04), so a town in Maine,
Vermont, or Rhode Island gets its state's figures from its first day: its
config needs only the package's keys.

**Matters at:** now.

#### Readers for more platforms

Each reader is written once for every town on the platform, so the order
follows the towns chosen.

| Platform | Reads | Towns |
|---|---|---|
| CivicPlus calendar, month by month, and Archive Center | done (v1.31.0 for month by month) | Gloucester; Beverly, Lawrence, Malden beside their Agenda Centers |
| CivicPlus Agenda Center | done | Malden, Beverly, Lawrence; Lowell next |
| CivicClerk | done | Manchester; Springfield, Medford, Burlington, Portland, South Kingstown |
| DotNetNuke | done | Manchester |
| Google Drive folders | done | Gloucester's School Committee |
| Town website file list | done | Wallingford |
| Finalsite district boards, with Google Docs | done (v1.22.0; the district's schedule, v1.31.0) | Wallingford's Board of Education |
| A district's iCalendar feed | done (v1.31.0) | Beverly's School Committee (Edlio) |
| A page of a board's dates | done (v1.31.0) | Malden's School Committee (Finalsite) |
| Rhode Island Open Meetings portal | not yet; about two days | every RI public body |
| Govstack, older Drupal CivicPlus | not yet; about two days each | several surveyed cities |
| Agenda Centers that need JavaScript | not yet | Framingham, Melrose |
| Diligent Community, BoardDocs, ParentSquare, Campus Suite | not yet; small each | school boards (Burlington, Portland, others) |

Free full text (`pipeline/pdftext.py`), so documents aren't transcribed or
re-read by the model: Legistar's style done; Foxit (Manchester's) next.

#### State packages

A state's package (`pipeline/states/<state>/`) reads its tax bill, budget,
school, and housing figures, fetched once for the state where it can be.

| State | Tax bill | Budget | Schools | Housing | How |
|---|---|---|---|---|---|
| Massachusetts | done | done | done | Subsidized Housing Inventory, per town | DLS once for every town, into `states/ma/` |
| New Hampshire | done | done | done | | Yearly files saved by hand into the engine (`pipeline/states/nh/figures/`) |
| Connecticut | done (calculated) | done | done (EdSight) | Appeals List, after | data.ct.gov: one query covers all 169 towns; the yearly parcel file found by name |
| Vermont | done (calculated, homesteads) | done | done | | Yearly workbooks found and saved into the engine by `pipeline.states.vt.extract`; VCGI parcels and data.vermont.gov at each run |
| Maine | done (calculated) | done | done | | MRS's yearly summary PDF and the ESSA Dashboard's Tableau export, saved into the engine by `pipeline.states.me.extract`; the Maine GeoLibrary's parcel table at each run |
| Rhode Island | none possible | done | done (absenteeism without the state's) | | The Division of Municipal Finance's PDFs saved by hand into the engine (`pipeline.states.ri.extract`); RIDE's report card files and assessment portal at each run |

Connecticut's tax bill and budget, and the Maine, Vermont, and Rhode Island
packages, were built on 2026-10-04, before each new state's first town, from
each state's own statewide sources, tested from the network's container. Each package's docstring lists its config keys.

**Connecticut, what's left.** The Affordable Housing Appeals List: data.ct.gov
has it through 2023 (`3udy-56vi`), and newer years only as a yearly PDF or
.docx (Wallingford 5.15% in 2025). Wallingford's config needs its `[finance]`
keys (`opm_town = "Wallingford"`, `opm_code = 148`, `single_family_use =
["1010"]`) and a budget section, on the release with the package. Connecticut
replaced its counties with planning regions in 2022, so county codes change
midway through every history (Wallingford is `0917078740` in the Census now,
`0900978740` before); OPM's datasets use town codes, so the package isn't
affected.

**Vermont, what's left.** Test results before spring 2025 are only in the
Agency of Education's yearly zip files (about 20 MB each); the extract could
read them once. Spending per pupil starts in fiscal year 2025, when Vermont
changed how it weights pupils. Vermont has no single-family category, so the
average bill is for homesteads on less than six acres (category R1), condos
and two- to four-family homes included, before the income-based property tax
credit.

**Maine, what's left.** The parcel table has values for only about 170 of
Maine's municipalities, sent when each chooses to, so a town's average bill
depends on its own submission being current; Lewiston's adds up to 1.10 times
its 2024 taxable land and buildings. The ESSA Dashboard's export isn't a
published API; the extract also reads the crosstabs downloaded by hand. Test
results start in spring 2023. There's no statewide source for a town's
adopted budget.

**Rhode Island, what's left.** The state publishes no average bill and no
statewide file of assessed values, so a Rhode Island town has no tax bill on
its home page. The Division of Municipal Finance's site is behind a
JavaScript challenge, so its yearly rate, levy, and assessed value PDFs are
downloaded by hand in a browser; fiscal year 2022's rates and 2022 to 2025's
levies and values haven't been. RIDE publishes no statewide all-students
chronic absenteeism rate in its data files.

**Wallingford, what else is there.** The town owns its electric, water, and
sewer utility, and its Public Utilities Commission meets twice a month: news
none of the other towns have. The council's "Agenda and Backup" packets are
scans of 24 to 117 pages, some over the 100-page summary limit. No 311
(SeeClickFix has 134 resident reports since 2016, none answered).

### For readers

#### Meetings, complete and correct

**Why.** The meetings are what most readers come for, so every one the city
lists should be here, once, as the city lists it, and a listing that's wrong
should say so rather than be copied.

**Found.** The meetings audit (2026-10-02, by hand, every town against its
city's own sites). Each live page matched its data, and no meeting was shown
at the wrong date or as scheduled after being cancelled. But towns that read
only an Agenda Center left out meetings the city lists before an agenda is
posted (Beverly about 12, Lawrence's City Council three times); Gloucester's
calendar feed reached two weeks ahead; Malden's Housing Authority posts each
agenda twice; Lawrence posts revised agendas and cancellations as new
postings; school boards were missing or stopped at the last post; a
Manchester entry was left over from a board's old schedule, another linked a
blank template as its agenda, another gave 3:30 AM; and Wallingford was
called a city.

**Done** (engine #52, publick.org #40, released as v1.31.0):
- CivicPlus calendars read month by month, three months ahead
  (`[meetings.civicplus]`), beside the Agenda Center where a town has one.
- One meeting listed in several places shown as one (`pipeline/listings.py`):
  the same board and day, at the same time, or one listing without a time and
  the titles agreeing on the kind of meeting; the newest posting of each
  source gives its status, a cancellation anywhere standing. `python -m
  pipeline.listings` says what was put together and why.
- Corrections (`[[meetings.corrections]]`): shown on the meeting's page with
  the reason, the evidence and the date checked; a meeting that most likely
  won't happen stays listed, marked "May not take place"; a correction comes
  down when the city changes the listing. The first: Manchester's Arts
  Commission on November 9.
- Honest labels: "Agenda posted" only for a link to the meeting's own agenda;
  a time in the night shown as unclear, with what the listing says; each
  source named in the meetings page's footer.
- "The town" for a town, in English and Spanish, from the Census Bureau's
  word for the place (`pipeline/fetch_place.py`).
- School boards: Wallingford's from its district's schedule, Beverly's from
  its district's calendar feed, Malden's from its district's page of dates,
  Gloucester's schedule read 75 days ahead.
- The home page: up to six of the week's meetings in full (the main boards,
  then what has a summary or a hearing, then the soonest), the rest a tap
  away; three recent decisions; the sections as one row of links. Every
  town's page is shorter on a phone than before the new meetings: Gloucester
  5.8 to 4.3 screens, Wallingford 4.7 to 3.8.

**Next.**
- The first daily run's check, and an audit by hand about weekly while towns
  are added (decided: it stays by hand).
- Manchester's school board: its site stops automated reading, so ask the
  district to allow Publick's crawler (see [Not planned](#not-planned)).
- Manchester's Highway, Parks and Police commissions, whose dates are only
  in earlier agendas and the police calendar: read the police calendar if
  it's simple; otherwise leave them.
- Beverly posts agendas early, so most of its week has summaries; if its home
  page grows in a heavy week, the six-meeting rule already holds it.

**Matters at:** now.

#### Saying why something is missing

**Why.** A reader can't tell "the town hasn't posted it" from "Publick
missed it", and a gap with no reason reads as a broken site, even when the
data is right. Raised looking at Wallingford's site (2026-10-04): no
ward map (the town has no wards) and meetings that looked incomplete (the
records matched the town's own page).

**Found** (2026-10-04, every template, and every town's data from the
10-03 run, on engine v1.30.0 data):
- *Wrong statements.* "Minutes not posted yet" on 53 cancelled meetings
  and on 75 Manchester calendar-board meetings whose minutes are never
  collected; "A summary will be added" for documents before `[summaries]
  since`, or in a town without `[summaries]`; "No agenda was posted" for any
  past meeting without one, including those before Publick collected
  agendas; "No public meetings are listed" when the calendar couldn't be
  read; a board's "Meetings recorded here start in" giving the site's first
  month, not the board's.
- *Gaps with no reason* (out of 1,125 held meetings): 644 without minutes,
  453 of them over 60 days old; 928 without a time and 1,002 without a
  place, most from sources that list neither; 698 with nothing summarized.
  Sections, maps and figures that a town doesn't have are left out with
  nothing said (no wards, no 311, no state budget figures), and freshness
  is shown only on the About page.
- Every gap has one of six reasons, and the engine knows which at build
  time: not posted yet (and for how long), the source doesn't include it,
  Publick doesn't collect it (a start date, a board, a size limit), held
  back by a check, Publick's copy is behind, or it doesn't apply to the
  town (no member has a ward, so every seat is at-large).
- Also found: 408 of 481 summaries are shown in English on the Spanish
  pages, and about 70% of translations fail their review (see
  [Spanish quality](#spanish-quality)).

**Done.**
1. The wrong statements fixed (#72): meetings not held, boards whose
   minutes aren't collected, minutes not posted (with where and for how
   long), summaries that won't come, agendas a listing doesn't link, a
   calendar not read lately, a board's first month.
2. The reasons from one module, `pipeline/absences.py`, from what the build
   knows; each sentence written and translated once. Also: a section page
   says which of its sources are behind; the Officials page says when every
   seat is at-large; the About page lists the sections a town doesn't have
   and why, with `[absences]` for the town's own sentence; every table's –
   has a legend; a saved agenda without a summary says whether one is coming.
3. Site checks fail a page with an unexplained –, an empty Agenda or minutes
   section, minutes "coming" for a meeting not held, an Officials page with
   no ward map and no reason, or an About page missing the sections a town
   lacks.

**Next.**
- `[absences]` sentences for Beverly, Lawrence and Wallingford's 311, from
  their configs' own notes.
- Lists (past meetings, Decisions, search, the street lookup) say what they
  leave out: unsummarized documents aren't searchable or in Decisions yet.
- Decisions held back by the fact check counted on the Decisions page, as
  on the meeting page.

**Matters at:** now; every town added without it adds gaps nobody explains.

#### Who represents you

**Why.** Among the first things readers look for, and what vote records need.

**Done.** The Officials page (`pipeline/officials.py`), from each town's
`[officials]` table: each body's members with seat, term end, and official
email; who represents each ward; and the ward map, whose "Find my ward"
checks the visitor's location in the browser without sending or saving it.
A seat can be elected by several wards. Every town lists its mayor, council,
and school committee.

**Next.**
- Each town's next municipal election in its config, and the status page
  flagging a list not checked since. Today the `checked` date is shown and
  never compared with anything (`officials.py:43`).
- The street lookup says which wards a street runs through.
- At 20 to 50 towns: a check that compares each town's list with its city's
  own council and school committee pages (most are CivicPlus, so one reader
  covers many) and opens one issue listing differences for a person. It
  never changes a list by itself.
- MassGIS's Wards and Precincts file covers every Massachusetts municipality:
  fetch it once with the statewide sources and cut each town's wards from
  it.

**Matters at:** now.

#### Sites in Spanish

**Done.** Every town in English and Spanish (v1.23.0 for Lawrence, v1.27.0
for all, v1.29.0 for any number of towns):
- Spanish under `/es/` on the same address, served by the same Worker; each
  page links its other-language version, with `lang` and `hreflang`.
- The homepage opens in the browser's first language; every other address
  opens as asked, so a shared link opens in the language it was shared in.
  The header switch remembers the choice over the browser's.
- About 10,000 words of the sites' wording in one string file per language,
  English pages unchanged; the Spanish written once, with a style guide
  (`site/strings/es-guide.md`).
- Summaries translated from the English summary, never the PDF, by Haiku 4.5;
  each translation keyed by the English it came from, so turning Spanish on
  regenerates nothing and a translation is made again only when its English
  changes. Translations count in the budget in the same priority order.
- Agendas, minutes, and transcripts stay in English, the official record.
  Board names in Spanish with the official English after them ("Concejo
  Municipal (City Council)").
- Search and the site checks cover both languages.

**Next.** Everything under [Spanish quality](#spanish-quality); then other
languages, if decided (see [Ideas to decide](#ideas-to-decide)). Browser
translation covers the rest.

#### Open data

**Why.** The summaries and decisions are useful beyond Publick's pages: to
local newsrooms, researchers, civic groups, and apps. Publick wants credit
when they're reused.

**Today.** Everything is public, not packaged for reuse:
- The network repository holds each town's `data/meetings/meetings.json` and
  `data/summaries/*.json`, one per summarized document, named by the
  document's hash, so joining them to meetings is left to the reader.
- Each site publishes `/meetings/data/decisions.csv`, `/feed.xml`,
  `/meetings/search-index.json`, and permits and 311 tables as CSV.
- No license for the data, so reuse is unclear. The engine's code is MIT.

**The license (decided 2026-10-01): CC BY 4.0** for what Publick makes: the
summaries, headlines, decision lists, and data compiled from public sources.
The credit asked for: "Summary by Publick (publick.org), AI-generated from
[the source document, linked]". What isn't Publick's keeps its own terms:
311 data stays under SeeClickFix's CC BY-NC-SA 3.0; agendas and minutes are
public records; Census and BLS figures are public domain. Text written
entirely by a model may have little copyright protection in the US, so
credit rests more on custom than on law.

**Next.**
- *Done 2026-10-03:* the network repository's `LICENSE` says in plain words
  what's Publick's (CC BY 4.0, full text in `LICENSE-CC-BY-4.0.txt`), how to
  credit it, and what keeps its own terms (311, public records, Census and
  BLS, state figures, maps); its code is MIT, like the engine's. Every About
  page has "Reusing what's here" with the credit line, and every footer says
  the summaries and data are free to reuse with credit, on network sites
  only (`site.network`).
- *Now:* SeeClickFix's terms, saved as read when the license was written.
- *Then:* a per-town export, `/data/meetings.json`, in a fixed, documented,
  versioned format: each meeting with its board, date, status, links,
  summaries, and decisions joined to it, built with the site as a static
  file. A Data page on each site describing the files and the credit line,
  and a list of every town's export on publick.org. Costs left out.
- *On the page:* summaries are AI-generated and can be wrong; the town's
  documents are the official record, and every summary links its source.

**Matters at:** the license now; the export before telling anyone the data
is there, and after the summary checks.

#### Accessibility

- "Every chart has a table" (`accessibility/index.html:14`) is still false
  for the 311 category and ward pages. Add the tables.
- *Done 2026-10-03:* every table that scrolls is reachable by keyboard.
  `site/static/js/tables.js` gives a `.table-wrap` that is wider than the
  screen a tab stop, the region role, and a name (the table's caption, else
  the heading before it, so no new wording to translate); one that fits gets
  none, so a wide screen's tab order doesn't grow. At normal text size only
  the Officials and About tables scroll on a phone; at 200% text every table
  does. Before, 1 of 32 could take focus (About's, labeled in its template).
- Phone layouts: v1.24.0 and v1.28.0 fixed overflow found by the checks;
  keep the phone widths in every check.
- Dark styles, if ever: then the dark-mode accessibility tests come back.

### Scale

#### Data out of git

**Why.** Collected data is committed daily. A town's data folder is 8 to 13
MB, most of it 311 requests, much of it rewritten every run. At a thousand
towns that's about 10 GB of working data changing by gigabytes a week, and a
thousand jobs a day pushing to `main` means constant conflicts.

**Done.** Each fetching run records the size of the town's data and what
the run added (`data/run.json`).

**Next.** Keep data files line-stable (sorted keys, one field per line).
311's raw requests to R2 first, before the next 311 town; then each town's
working data, as agenda and minutes PDFs already are, with git keeping config
and code and sites built from the bucket. Every town has a `[storage]`
table. Also before the next 311 town: commit each town's data as soon as it
finishes, so a timeout loses nothing (the commit step is skipped on a
timeout today, `network.yml:387`).

**Matters at:** 20 to 50 towns, sooner for a big 311 history.

#### Shared sources once per state

**Why.** Statewide and national sources downloaded separately for every
town means about 350 Massachusetts towns asking DLS and DESE every day. DLS
already refuses GitHub's addresses some days, SeeClickFix allows about 20
requests a minute and has blocked us, and the keyless BLS API allows a
couple of dozen requests a day per address.

**Done.** New Hampshire's yearly files saved once into the engine.
Massachusetts's DLS reports (tax bill, budget figures, parcel counts) fetched
once for every municipality by the statewide step (`pipeline.network
states`, `pipeline/states/ma/dls.py`) into `states/ma/`: 27 requests cover
all 351, against about 20 per town before. If DLS refuses, towns keep what
was saved, and three failures in a row put the state in the daily alert.

**Next.**
- *Phase 2:* the Subsidized Housing Inventory (one statewide PDF every town
  downloads whole) and DESE's school figures (its portal answers statewide
  queries), into `states/ma/`. MassGIS's ward file too.
- *Phase 3:* BLS unemployment (up to 50 series a request) and the Census's
  permits and estimates, once for the country.
- *Per vendor:* SeeClickFix paced across all towns that use it (pacing is
  per town today, `fetch_311.py:251`), and the same for meeting portals.

**Matters at:** tens of towns in one state; SeeClickFix before the next 311
town.

#### Sources on their own rhythm

Done (v1.8.0): each figure source has a rhythm (`pipeline/rhythms.py`):

| Rhythm | Sources | Checked | Behind when |
|---|---|---|---|
| Continuous | Meetings, agendas, minutes, school documents, 311, a city's permits | Every run | A check fails, or data over 2 days old |
| Monthly | Unemployment (BLS), permits so far this year | Weekly | The next month is two months past its usual date |
| Yearly | Tax rate and bill, budget, school figures, annual permits, ACS, parcels | Monthly; weekly near a release | The next period is two months past its usual date |

`pipeline.update` skips a step that isn't due; `pipeline.freshness` judges
figures by period, so a refused check of the latest certified year isn't
"behind"; the About page shows each source's latest period and the next
one's usual date; DLS retries a 202 with nothing. Left: the single-town
workflow, `town.yml`, still runs every step daily.

#### Runner capacity and the work queue

**Why.** Once backlogs clear, a town's daily job takes about 15 minutes, so a
thousand towns need about 250 runner-hours a day: about 12½ hours at 20 jobs
at a time, with no room for a retry. A new town's first 311 history takes
about 40 minutes a run for weeks. The longest of the last 100 runs took 179
of its 300 minutes.

**Done.** One town per job on manual and pull request runs; browser checks
on every core (Manchester's full check from 481 to 168 seconds).

**Next.**
- The matrix's 256-job limit checked in `plan()` (`network.py:145-166`).
- At about 100 towns: a work queue. The scheduler adds whatever is due
  ("Malden meetings", "Massachusetts tax bills", "Manchester 311"), and
  workers take items under per-vendor rate limits. A late run only means a
  longer queue. Workers stay GitHub jobs, or move to Cloudflare or a small
  server, whichever is cheaper then.
- A paid GitHub plan (60 concurrent jobs on Team) buys time without changing
  the design.
- A new town's 311 history fetched by its own job over its first weeks.

**Matters at:** about 100 towns on the free plan.

#### Who can change what

**Why.** In one repository, anyone with write access can change every town.
Fine while Publick runs every town itself.

**Next.** `CODEOWNERS` names who reviews each town's folder, and branch
protection requires that review. A town that wants to run its own site gets
its own repository calling `town.yml`.

**Matters at:** the first editor from outside Publick.

## Reference

### How the network runs today

Towns live in one repository,
[publick-org/publick.org](https://github.com/publick-org/publick.org) (decided
and done). The engine stays its own repository with its own tests and
releases.

```
publick-org/publick.org
  engine-version              the engine release every town runs (v1.30.0)
  ADDING-A-TOWN.md            the checklist for a new town
  towns/<town>-<state>/
    config/<town>.toml
    data/                     with run.json (the last fetching run) and
                              summary-costs.json (summary costs, by month)
    site/static/share/<town>.png
  states/ma/                  statewide sources, fetched once for every town
  home/                       publick.org, and a page per state with 10+ towns
  scripts/                    build_home.py, build_status.py
  wrangler.toml               the Worker that serves every site
  wrangler.scheduler.toml     the Worker that starts the daily runs
  .github/workflows/network.yml, worker.yml
```

The engine reads a town's config, data, and static files from
`PUBLICK_TOWN_DIR`, so each town's commands run unchanged with it set to the
town's folder, each town in its own process (`pipeline/network.py`).

**The daily run.** The `publick-scheduler` Worker starts `network.yml` every
hour from 09:05 to 14:05 UTC; GitHub schedules in the same hours are a
backup.
1. A plan job takes the towns that are due (last fetching run over 18 hours
   ago), oldest first, splits them into jobs (four towns a job on a daily
   run, one otherwise), and shares out what's left of the month's budget.
2. A statewide job fetches what every town in a state shares, once, into
   `states/` (only what isn't saved or is over a week old).
3. Town jobs run in a matrix, each checking out only its towns' folders and
   `states/`, with Python packages and Playwright installed once per job.
4. Each town fetches, is built and checked (a sample of pages on a daily
   run, every page on a pull request), and is published on its own if its
   checks pass; a town that fails keeps its last good site. Each job commits
   its towns' data, retrying against the others' pushes.
5. A report job writes one table of every town. A daily run doesn't fail for
   a town; a pull request's run does.
6. The homepage and status page are rebuilt and published, and the "Towns
   need attention" issue is opened, updated, or closed.

A pull request that changes a town's folder builds and checks that town; one
that changes `engine-version` or a workflow builds and checks every town. A
manual run can take any towns, with or without fetching, and only some
sources.

**Hosting.** Every site is at `<town>-<state>.publick.org`: one wildcard DNS
record points at one Worker, which finds the town from the hostname, looks
up its current build in R2 (files stored once by content, shared across
sites), and serves the file. A deploy uploads the build, then points the
town at it; rolling back points it at the previous build; builds beyond the
newest ten are deleted daily. Agenda and minutes PDFs are in a second
bucket, `publick-documents`, served at files.publick.org.

**Secrets.** One set, nothing per town: the Anthropic key, the storage keys,
the sites bucket keys, the BLS key, the Cloudflare deploy token, and
`SCHEDULER_GITHUB_TOKEN` (fine-grained, the network repository's Actions and
Issues, made 2026-09-30 for 366 days).

**Page views.** One GoatCounter site, `publick` (no cookies, never what was
searched); each town's `[analytics] prefix` is its folder name, so one
dashboard tells the towns apart.

**What this replaced.** One repository per town, with staggered cron lines,
a command to create repositories and Pages settings, warnings for towns on
odd engine versions, and re-enabling workflows GitHub turned off after 60
quiet days.

### Done, by release

| Release | What |
|---|---|
| before v1.7 | The network repository, with the first three towns moved in; the status page; automatic releases; one town per job on manual runs, checks on every core |
| v1.7.0 | Manchester's summaries, and the agendas its calendar links |
| v1.7.1 | Runs don't lose data when two update the same town |
| v1.8.0 | Figure sources on their own rhythm; the DLS retry |
| v1.9.0 | Page views for every town on one GoatCounter site |
| v1.10.0 | The $50 network budget, its ledger and priority order; one daily alert; a town's failure doesn't fail a daily run; data size in the run record |
| (tests) | Whole-site test builds for CivicClerk, DotNetNuke, and Agenda Center towns |
| v1.12.0 | Daily runs take the towns that are due, started on time by the scheduler Worker, which watches that they finish |
| v1.13.0 | Massachusetts's DLS reports fetched once for every town |
| v1.14.0 | The Officials page |
| v1.15.0 | Towns without 311; seats elected by several wards |
| v1.16.0 | Summaries without retyped documents; free full text where the style is supported |
| v1.17.0 | Vote records collected, not shown |
| v1.17.1 | Minutes for Agenda Center and CivicClerk towns; summaries of up to 100 pages |
| 2026-10-01 | Beverly, the first town added from scratch; the first full daily cycle checked |
| v1.18.0 | The homepage by state, with a page per state at 10 towns |
| v1.22.0 | School board meetings from a Finalsite district website; Wallingford live, the first Connecticut town |
| v1.23.0 | Sites in Spanish; Lawrence live in both languages (2026-10-02) |
| v1.24.0 | Meeting times fit their column |
| v1.27.0 | Every town in Spanish |
| v1.28.0 | Monthly charts fit a phone |
| v1.29.0 | Spanish at any number of towns: shared words once, machine drafts for the rest; every summary on the current prompt version |
| v1.30.0 | The open redirect closed; the Spanish disclosed as machine-translated and checked without a person (names, outcomes, amounts, and a second model's review); a missing Spanish text never stops a build; releases once a day; a republished town's run record; light-only accessibility tests (2026-10-02) |
| v1.31.0 (2026-10-03, never reached the towns) | Summaries checked against their documents' own text: what isn't in the document isn't shown, and vote counts it doesn't give are left out (#50). The meetings audit (#52): CivicPlus calendars month by month; one meeting listed in several places shown as one; corrections shown openly; "town" for a town, from the Census; each meeting source named; school board schedules and calendar feeds; a home page of six meetings in full. "AI summary" on every summary shown in a list, and in the feed; "Reusing what's here" on every About page, with the data license (#54). A SeeClickFix block stops the 311 step instead of marking requests removed (#56); the 311 pages dated by the last fetch (#57); an emptied calendar fails, and `behind --new-since` for the alert's comment (#58); `[summaries] since` (#59); a check of each live site after publishing (#60) |
| v1.32.0 (2026-10-03) | `network.py` loads with the standard library only again (#62); the scheduler starts the release and the engine pull request (#61) |
| v1.33.0 (2026-10-03) | The browser checks leave out a moved meeting's page, which redirects itself (#63). Every town on it from 13:09 UTC (publick.org #43) |

The October 2026 outside review (2026-10-02) read both repositories and the
live sites; its plan, `REVIEW-PLAN.md` (commit e3963ee), and the second
review the same day are folded into the sections above.

### Where the old numbered items went

Earlier versions of this file numbered its items 1 to 18; commits refer to
them.

| Old item | Now |
|---|---|
| 1. Data grows in git | [Data out of git](#data-out-of-git) |
| 2. Shared sources | [Shared sources once per state](#shared-sources-once-per-state) |
| 3. Monitoring | [Monitoring](#monitoring) |
| 4. AI summary costs | [AI summary and translation costs](#ai-summary-and-translation-costs) |
| 5. A bad release | [Releases](#releases) |
| 6. Who can change what | [Who can change what](#who-can-change-what) |
| 7. Every source every day | [Sources on their own rhythm](#sources-on-their-own-rhythm) |
| 8. Scheduled runs | [Scheduling](#scheduling) |
| 9. Runner capacity | [Runner capacity and the work queue](#runner-capacity-and-the-work-queue) |
| 10. Adding a town | [Adding a town](#adding-a-town), [Next towns](#next-towns), [Readers](#readers-for-more-platforms), [State packages](#state-packages) |
| 11. Who represents you | [Who represents you](#who-represents-you) |
| 12. Vote records | [Vote records](#vote-records) |
| 13. Meeting video summaries | [Not planned](#not-planned) |
| 14. Sites in Spanish | [Sites in Spanish](#sites-in-spanish), [Spanish quality](#spanish-quality) |
| 15. Open data | [Open data](#open-data) |
| 16. Releases and runs churn | [Releases](#releases) |
| 17. Summary checks | [Summary checks](#summary-checks) |
| 18. Steps done by hand | [Steps done by hand](#steps-done-by-hand) |
