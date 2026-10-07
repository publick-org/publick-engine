# Roadmap

Last cleaned up 2026-10-05, at seven towns on engine v1.36.0. The work
finished from 2026-10-02 to 2026-10-05 is now in the theme sections and
under [Done, by release](#done-by-release). The priority list starts again
from what's left. Brought up to date 2026-10-07: ten towns on v1.42.0, the
weekly digest live for Gloucester, and getting the sites ready for more
readers.

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
  Hampshire, Connecticut, and Vermont have towns; Maine and Rhode Island's
  first are in a pull request (publick.org #58). About 20 towns, each
  launched in English and Spanish.
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
  that says when its data is behind, and why something is missing.
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
budget of **$80 a month**. One person runs and merges everything; about two
thirds of commits are written by AI.

## Where things stand (2026-10-07)

| Town | Live | Meetings from | 311 | Notes |
|---|---|---|---|---|
| Gloucester, MA | moved in | CivicPlus calendar (month by month) and Archive Center; School Committee in Google Drive, with its schedule | SeeClickFix | City permits file |
| Malden, MA | moved in | CivicPlus calendar and Agenda Center; School Committee dates from the district's page | SeeClickFix | Roll-call votes collected, not shown |
| Manchester, NH | moved in | CivicClerk and DotNetNuke | SeeClickFix | NH figures saved by hand; the school board's site blocks automated reading |
| Beverly, MA | 2026-10-01 | CivicPlus calendar and Agenda Center; School Committee from the district's calendar feed | none | Council minutes are scans |
| Wallingford, CT | 2026-10-01 | The town's own website; Board of Education on Finalsite, with its schedule | none | Connecticut's tax bill and budget since 2026-10-04 (publick.org #51); no wards |
| Lawrence, MA | 2026-10-02 | CivicPlus calendar and Agenda Center | none | Launched in English and Spanish |
| Burlington, VT | 2026-10-04 | CivicClerk | SeeClickFix | First Vermont town, from config alone (publick.org #54); the School Board is on Diligent Community, not read yet |
| Bangor, ME | 2026-10-05 | CivicPlus calendar and Agenda Center, with the School Committee's | SeeClickFix, no areas (from 2026-10-06, publick.org #64) | First Maine town; Maine's figures saved once a year |
| Lewiston, ME | 2026-10-05 | CivicPlus calendar and Archive Center; School Committee in Google Drive (#84) | none | |
| South Kingstown, RI | 2026-10-05 | CivicClerk | SeeClickFix, by voting precinct | First Rhode Island town; no tax bill, which the state publishes nothing to calculate |

- Every town is on engine v1.42.0, in English and Spanish. The 08:40 engine
  move of 2026-10-06 (v1.38.0) was held, as it should be: Lewiston's home
  page failed a check (a two-digit day, "Oct 12", wider than its column), so
  nothing merged and the towns stayed on v1.37.0. #83 fixed the layout, and
  v1.39.0 to v1.42.0 were moved the same day, mostly by hand (publick.org
  #63, #65, #69, #71).
- The weekly digest is live for Gloucester (engine #85 to #87, publick.org
  #67, #68, #70; Workers deployed 2026-10-06 at 18:07 UTC): the signup form
  is on, and the first email goes out Sunday 2026-10-11 at 5:30 PM. The other
  nine towns are in publick.org #72, after that send works.
- HSTS is live from the sites Worker (deployed 2026-10-05).
- 661 summaries, 402 with a Spanish translation saved. Spanish summaries by
  Claude Sonnet 5.5 since #82 (v1.38.0). October's summaries and translations
  after six days: $16.88 (Beverly $3.28, Manchester $2.65, Malden $2.40,
  Burlington $2.29, Gloucester $1.71, Bangor $1.40, Wallingford $1.23,
  Lewiston $0.77, South Kingstown $0.71, Lawrence $0.44).
- Fact checks (the 2026-10-06 run records): 19 of 654 summaries held back
  (Bangor 5, Burlington 5, Gloucester 3, Lewiston 3, Malden, South Kingstown
  and Wallingford 1 each); 66 not yet checked, most in Beverly (23), Malden
  (20) and Gloucester (19).
- "Towns need attention" (publick.org #66) is open for Beverly, whose
  summaries are behind.
- 311 history in git: about 30 MB across five towns (Malden 13, Manchester
  9.3, Gloucester 6.2, Burlington 1.8, South Kingstown 0.2), plus Bangor's
  from 2026-10-06.
- Network status: [publick.org/status/](https://publick.org/status/).

## Priorities

Each line points to its section under [The work, by theme](#the-work-by-theme).
Order within a group is the order to do them in. The list was renumbered on
2026-10-07; [Where the old numbered items went](#where-the-old-numbered-items-went)
maps the October list's numbers.

### Now: this week

Done since 2026-10-05: Lewiston, Bangor, and South Kingstown live
(publick.org #58), HSTS live (**Actions → Worker**, 2026-10-05), and
Burlington caught up (#79).

1. **Ready for more readers.** Every request to every site runs the sites
   Worker, and Workers' free plan refuses requests past 100,000 a day, for
   the rest of the day, on every site at once: a few thousand readers from
   one news story would take the network down until midnight UTC. Move the
   Cloudflare account to Workers' paid plan ($5 a month, 10 million requests;
   it also lifts the digest send's 50-request limit). Then deploy the sites
   Worker's hardening (engine `claude/worker-hardening`, with publick.org's
   `SIGNUP_LIMITER`): files kept in Cloudflare's cache, a plain 503 when the
   bucket fails, no framing by other sites, and a rate limit on the digest
   signup. ([Security and privacy](#security-and-privacy))
2. **The first digest email**, Gloucester's, Sunday 2026-10-11 at 5:30 PM
   (the scheduler's 21:35 UTC run). Before then: Buttondown's tags add-on
   and the footer's mailing address; a test address signed up, confirmed,
   and tagged `gloucester-ma-en`. After: check the email and its links, then
   publick.org #72 for the other nine towns. ([Decided, not scheduled](#decided-not-scheduled))
3. **A switch to hold back one summary.** When a reader reports a wrong
   summary, the only way to take it down today is rolling the whole town
   back, and the next daily run publishes it again (`RUNBOOK.md`, "A reader
   reports an error"). With more readers this is the gap that matters most.
   Built in #90: a `[[summaries.withheld]]` entry in the town's config takes
   one meeting's agenda or minutes summary down everywhere, and its page says
   so. ([Summary checks](#summary-checks))
4. **A date test before each release.** The engine move of 2026-10-06 was
   held because a two-digit day overflowed the home page's column in
   Lewiston (fixed in #83): the network's check caught it, as designed, but
   only once a town had a meeting on the 12th. The engine's own tests should
   render meetings on the widest dates (two-digit days, the longest month
   and weekday names, in Spanish too) so the release never carries one.
   ([Releases](#releases))
5. **Beverly's summaries caught up** (issue #66), and the 66 summaries not
   yet fact checked. ([Monitoring](#monitoring))
6. **Read what the fact check holds back**: 19 summaries across seven towns,
   most in Bangor and Burlington (5 each). Each one wrong in the summary, or
   the check's mistake? Fix the check if it's the check's.
   ([Summary checks](#summary-checks))
7. **Spanish translations, measured again** now that Sonnet 5.5 translates
   (#82): the 2026-10-04 audit found about 70% failing their review. Read a
   sample of what the review flags: a real error is the prompt's to fix, a
   false flag the review's; never loosen the review to pass more.
   ([Spanish quality](#spanish-quality))
8. **The meetings audit, by hand**: every town's upcoming meetings against its
   city's own sites; the last was 2026-10-02, and Burlington, Bangor,
   Lewiston, and South Kingstown have never had one.
   ([Meetings, complete and correct](#meetings-complete-and-correct))

### Then: to about 20 towns

9. **311's raw requests in R2, and one SeeClickFix job paced across towns.**
   Planned for before the next 311 town; Burlington went live with 311
   without it, and South Kingstown (#58) will too. Before any further 311
   town. ([Data out of git](#data-out-of-git),
   [Shared sources](#shared-sources-once-per-state))
10. **Lowell**: config only. ([Next towns](#next-towns))
11. **Saying why something is missing, the rest**: `[absences]` sentences
   for the towns without 311, lists that say what they leave out, held-back
   decisions counted on the Decisions page.
   ([Saying why something is missing](#saying-why-something-is-missing))
12. **Springfield**, after Medford: CivicClerk with meetings sorted into
    boards. ([Next towns](#next-towns))
13. **The open data export**: each town's `/data/meetings.json` and a Data
    page. ([Open data](#open-data))
14. **The next readers**: Foxit full text (Manchester's minutes, the most
    common style not read free); school boards on Diligent Community
    (Burlington) and BoardDocs; nested Google Drive folders (Lewiston's
    School Committee); then the next platform a chosen town needs.
    ([Readers](#readers-for-more-platforms))
15. **Officials kept current**: each town's next election date in its config,
    with the status page flagging a list not checked since.
    ([Who represents you](#who-represents-you))
16. **Vote records shown**, for Malden's council first, after a few weeks of
    a person checking every new vote. ([Vote records](#vote-records))
17. **Statewide sources, phase 2**: the Subsidized Housing Inventory and DESE,
    once for Massachusetts. ([Shared sources](#shared-sources-once-per-state))
18. **The helper for adding a town**, which also runs the first fetches and
    sets up email routing. ([Adding a town](#adding-a-town))
19. **Statewide sources, phase 3**: BLS and the Census, once for the country.
    ([Shared sources](#shared-sources-once-per-state))
20. **Spanish, the rest**: `/es/feed.xml`, a Spanish share image, decision
    labels matched by content rather than position, and Ward and District
    kept apart in Spanish. ([Spanish quality](#spanish-quality))
21. **Wallingford's Affordable Housing Appeals List**, from Connecticut's
    package. ([State packages](#state-packages))
22. **Accessibility**: a table for every chart. ([Accessibility](#accessibility))
23. **Upkeep**: config keys only one town uses folded into their readers; a
    monthly page-view report. ([Upkeep](#upkeep))

### Stage 2: about 20 to 50 towns

24. Town data moved to R2, with git keeping config and code, when the run
    records' sizes say so. ([Data out of git](#data-out-of-git))
25. Canary towns (Manchester and Malden) on the newest release, and sampled
    checks when `engine-version` moves. ([Releases](#releases))
26. `CODEOWNERS` and required reviews, before the first editor from outside
    Publick. ([Who can change what](#who-can-change-what))
27. The scheduler Worker deployed when it changes, and reminders for the
    steps that stay by hand. ([Steps done by hand](#steps-done-by-hand))
28. Officials compared with each city's own pages, with differences opened
    as one issue for a person. ([Who represents you](#who-represents-you))
29. Summaries through the Batches API, at least for the backlog.
    ([AI costs](#ai-summary-and-translation-costs))

### Stage 3: about 100 to 1,000 towns

30. The work queue: sources due, per scope, with per-vendor rate limits, and
    a new town's history fetched on its own. ([Runner capacity](#runner-capacity-and-the-work-queue))
31. A paid GitHub plan or other workers, as the queue's length shows the need.
32. A summary budget sized to the network, with one priority order across
    towns. ([AI costs](#ai-summary-and-translation-costs))
33. A status page with search and filters, and a daily digest instead of an
    alert. ([Monitoring](#monitoring))
34. Self-hosted page counts, if GoatCounter's free use runs out.

### Decided, not scheduled

Kept so they aren't lost. Each comes into a stage when it's chosen.

- **A weekly digest for readers.** The pages and their feed are built
  (2026-10-06, `pipeline/digest.py`): each Sunday's issue at
  `/digest/<Monday>/`, with the week's meetings and the minutes posted the
  week before, no AI calls, and `/digest/feed.xml` dated for the email.
  Decided 2026-10-06: sent weekly, Sunday at 5:30 PM in the town's own time
  (a day's notice of Monday evening meetings, which are about a fifth of all
  meetings, and hours after the morning's run); one email account with a tag
  for each town and language rather than a list per town; open and click
  tracking off, with GoatCounter `?ref=` on the email's links to measure
  use; English first, Spanish later (Lawrence first). The email says what
  it holds first, and that its lines are written by AI once per section,
  above them. Decided 2026-10-06: Buttondown, pending its answers on API
  access and turning tracking off (half price for a registered 501(c)(3));
  the scheduler Worker sends each town's issue at its date through the
  provider's API; the signup form posts to the sites Worker, which adds the
  town and language tag and passes it on, so the pages' `form-action 'self'`
  stays. Done the same day: the account, sending from
  `hello@digest.publick.org` (its DNS records on the `digest.` subdomain),
  tracking off, and two keys, one for each Worker
  (`BUTTONDOWN_SUBSCRIBE_KEY`, which can't send, and `BUTTONDOWN_SEND_KEY`).
  Built: the signup (`[digest] signup`, `worker/digest.js`), the send
  (`DIGEST_TOWNS` in the scheduler, off while empty), and the About page's
  privacy text (#85, #86, #87; v1.41.0 and v1.42.0). Live for Gloucester
  since 2026-10-06 (publick.org #67, #68, #70; Workers deployed at 18:07
  UTC): its signup is on and `DIGEST_TOWNS` names it, so its first email
  goes out Sunday 2026-10-11 at 5:30 PM. Left, in order: Buttondown's tags
  add-on (+$9 a month; tags and metadata are both paid, and sending by town
  needs one) and the footer's mailing address (Buttondown's own, if they
  confirm it's allowed), before that Sunday; a test address signed up,
  confirmed, and sent the issue; then every town (publick.org #72). Past
  about 20 towns the send needs Workers' paid plan (50 requests a run on the
  free one), or a list of what's due built with the homepage. An `.ics`
  meetings calendar is still worth doing on its own.
- **Links to meeting recordings** on each meeting's page, where the town
  posts one (YouTube for Beverly and Wallingford, 1623 Studios for
  Gloucester). Simple, and useful without any summary of the video.
  Each board's page already links the channel that broadcasts its meetings
  (`video_url` in `[participation]`); a link per meeting is what's left.
  Wallingford's and its Board of Education's YouTube links are already
  collected (`video_id`), not shown.
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

Proposals, not decisions. Each says what it would take.

- **Follow an issue across meetings.** A project, an address, or a budget
  line comes up at several boards over months; a page per address or project
  that lists every meeting where it appears, from the documents' own text
  (addresses can be matched without AI). It's what readers who aren't at
  every meeting most lack.
- **How to take part, per board, the rest.** Each board's page and its
  meetings' pages already say how public comment works and where meetings
  are broadcast, from `[participation."<board>"]` in every town's config
  (publick.org #64). Still to decide: how to get on the agenda, and the
  clerk's contact.
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
- **How fast the town posts its records.** Per board: how far ahead each
  agenda goes up, how long minutes take, and which meetings still have none,
  beside the state's own deadlines (Massachusetts: notice 48 hours ahead not
  counting weekends and holidays, minutes approved by the later of 30 days or
  the third meeting after; New Hampshire: 24 hours, minutes in 5 business
  days; Connecticut: 24 hours, minutes in 7 days), never ranked against other
  towns (see [Not planned](#not-planned)). It measures *posted online*, which
  isn't compliance: a notice on the clerk's board or minutes kept at the
  clerk's office meet the law, so a page says "posted online", never "late"
  or "broke the law". When each document went up comes from the platform
  where it says so (CivicClerk's `publishOn`; Agenda Center's "Posted" time,
  kept today only inside `agenda_id`), and elsewhere from upload order
  (CivicPlus Archive Center's `ADID` and Wallingford's `FileID` rise as files
  are uploaded); PDF timestamps help but aren't evidence alone (Gloucester's
  main copier runs 11 hours slow). Measured by hand on 2026-10-04: Manchester
  posts agendas a median 5.1 days ahead and minutes 7.7 days after;
  Gloucester's agendas a median 6 days ahead, its minutes at least a median
  34 days after, 29 of 206 at least 90. What it would take: each document's
  posted time kept as its own field in `meetings.json`, a computation like
  `compute_311.py` per board counting only meetings after a board's first run
  (a backfilled town would otherwise show months of false delay), and a
  "Record keeping" block on each board's page. No AI calls.
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
- **Push runs that build but don't publish** (dropped 2026-10-03,
  publick.org #47 closed): a push keeps publishing, so corrections and engine
  moves reach readers the same day.

### Open decisions for the maintainer

- The order of the next towns (the lists under [Next towns](#next-towns) are
  a proposal).

Decided 2026-10-03 and 2026-10-04, and kept here for reference:

- 311 addresses already in git history stay as they are: no rewrite.
  `requests.json` keeps SeeClickFix's full addresses (the street lookup
  needs them); only what the pages show is cut to the block.
- The budget is $80 every month (publick.org #48).
- No second person with owner access; `RUNBOOK.md` says what one owner does
  (publick.org #45).
- Push runs keep publishing.
- State legislators on the Officials page, after the municipal work.

## The work, by theme

Each section: why it matters, what's done, what's next, and when it matters.

### Trust

#### Summary checks

**Why.** Summaries, headlines, and decisions (who moved and seconded, how
the vote went) are written by a model from agendas and minutes. A wrong
amount, name, or vote count reaches readers and, with open data, whoever
reuses it. Of everything shown, it's what can mislead a reader most.

**Done.**
- Prompt versions tracked, and a test that fails when a prompt changes
  without its version (`tests/test_prompt_versions.py`): each versioned
  prompt's words and schema pinned by a hash beside its version. The
  translation review's prompt has no version: a change there doesn't call
  for translating again.
- *The fact check* (`pipeline/factcheck.py`, #50, fact check version 2 on
  2026-10-03): every number, amount, date, name, vote count, and reference
  number in a summary, its headline, and each decision or agenda item is
  looked for in the PDF's own text, without AI. A decision, item, headline,
  or sentence with something not in the document isn't shown, and the page
  says how many decisions weren't; a vote count the document doesn't give
  (counted from a roll call) is left out. An amount counts when the document
  gives it with a scale ("$8K", "$1.2 million"). On the 449 summaries live
  then, every one checked against full text passed, and planted errors were
  caught 89 to 100% of the time.
- *Decisions anchored to quotes* (#66, minutes prompt version 3, fact check
  version 3, in v1.35.0): each decision has an outcome (approved, denied,
  tabled, continued, referred, recommended, withdrawn, or other) and the
  minutes' own words for it, in `decision_evidence` beside the decision. The
  quote must be in the document (spacing, punctuation, page headers, and line
  numbers ignored; a quote cut with "..." found part by part), and the
  outcome must agree with the decision's words and the quote's (a failed
  motion can't be approved, a two-thirds vote that failed 9-5 is a denial).
  What fails isn't shown, so a dropped "not" is caught. Numbers far from
  their quote are listed, not held. Minutes since 2026-08-04 were made again
  (`remake_since`); older ones keep version 2.
- *A test set against the real model*: `evals/minutes.json`, 15 minutes from
  six towns and 52 decisions checked by hand, run by `python -m
  pipeline.evaluate` (about $1 to $2) from the network's **Actions →
  Evaluate** (publick.org #44). On 2026-10-03 the model gave all 52 the right
  outcome, and all 119 planted errors were caught. The runs found the check's
  own mistakes, all fixed before it shipped.
- *Counted*: each fetching run's record (`data/run.json`, `fact_checks`)
  counts the town's summaries by result, how many are held back, and the
  decisions, items, and vote counts left out. `python -m pipeline.factcheck`
  lists each problem.
- *Labeled*: "AI summary" wherever a summary is shown in a list (#54): in the
  grey line under a meeting row's summary, in the public hearings box, and
  after the summary in the RSS feed; never as a badge in front. Spanish:
  "Resumen hecho con IA".
- Two summaries read by hand on 2026-10-03, both right (a typo copied from
  Beverly's minutes; "$8K" in a scan).

**Next.**
- Read what's held back in a new town's first runs (Burlington's 4 of 80, on
  2026-10-05), to tell the model's mistakes from the check's.
- For a scan, the check runs against the model's transcription, which is
  weaker since both come from the model; a failure is listed, not held.
- The document's own date and any "draft" marking read from it.
- Names in a decision checked against the body's `[officials]` members, as
  vote records are (from the October review's plan).
- A scan's decisions marked on the page as checked only against the model's
  transcription, not the document's own text.
- A switch to hold back one summary by hand, for a reader's report
  (`RUNBOOK.md` names it as a gap).

**Matters at:** now, before more towns and before open data.

#### Spanish quality

**Why.** Every town's site is in Spanish, translated by Haiku 4.5 and shown
without a person reading it. The second review (2026-10-02, v1.29.0) read all
77 saved translations beside their English and found meaning errors on live
pages that the check of the time passed: "adjourned" as "se disolvió",
"reappointment" as "reelección", guessed genders ("La presidenta Houseman"
for Scott D. Houseman), business signs as "señales", and Spanglish. The check
compared only digits, list lengths, and dates, so "voted not to approve" as
"para aprobar" or "failed 3-4" as "aprobada 4-3" passed, while correct
Spanish number formats failed. A failed translation was never retried,
older prompt versions were still shown, machine translation wasn't
disclosed, and the About page said "No cookies" beside the Worker's `lang`
cookie.

**Decided 2026-10-02: no person checks the Spanish.** A human reviewer isn't
realistic, so the sites say plainly that the Spanish is machine-translated,
always link the English, and two checks stand in for a person, falling back
to English rather than showing a doubtful translation.

**Done** (engine v1.30.0, 2026-10-02):
- Disclosure: a translated summary's credit says "Traducido automáticamente
  con IA del resumen en inglés; puede tener errores" and links the English;
  every Spanish page's footer says so too; agenda and minutes links say "(en
  inglés)"; the About page names the translation model and both checks; the
  cookie line says the language switch sets one cookie and nothing else.
- The check without AI, entry by entry: numbers kept and none added,
  whatever the Spanish number format; amounts' million or billion; a.m. and
  p.m.; names kept as written; outcomes not turned round (a "not" lost or
  added, denied as approved, tabled as approved, unanimous changed). Every
  crafted case from the review fails it.
- Claude Sonnet 5.5 reviews each translation that passes, and each batch of
  drafted town text, for meaning: about a cent each. Anything it flags shows
  the English.
- A failed translation or draft is made again once, then left; the page says
  the translation didn't pass. Older prompt versions aren't shown.
- The prompt (version 3): never guess gender; write dates with the month's
  name; a glossary for adjourn, appoint and reappoint, table, sign, name
  after, all-alcoholic license, underage operative.
- A config text with no Spanish shows in English with a warning instead of
  stopping the build, and drafting may spend $0.05 a run past the budget so
  the gap fills the next run.
- Gendered roles in the generic form where the holder changes (Lawrence's
  Vicepresidente).

*Done well from the start:* what's shown is keyed by the hash of the English
it came from and checked again at display; English fallback is marked
`lang="en"`; only `/` negotiates language, with `Vary` and a no-store 302;
`hreflang` and `x-default` set; a 404 per language; board names keep the
official English; Spanish search covers the translations.

*Done 2026-10-06 (translation prompt version 4):* most summaries had no
Spanish shown: 126 of 630, across ten towns. Of the translations made, about
190 failed the meaning review and 70 the check; about 245 had never been
made.
- Older summaries were translated only when the run hadn't stopped, and the
  backlog's English summaries spent the backlog budget first (Malden: "older
  documents wait: this run's $0.26 for them is spent", 131 of 157 never
  translated). Translations now come before the older English summaries.
- Haiku 4.5's translations failed the review more often than they passed:
  guessed genders (53), "Pospon", "posponemos", "redevelación". The
  network spent $2.77 on 390 tries for 126 shown, about 2.2 cents each.
  Claude Sonnet 5.5 translates now, at low effort, for about a cent with
  the review.
- The second try is a correction: the model gets its first translation and
  what the check or the review found wrong, rather than trying again blind.
- The review is given the translator's words and rules (it failed
  "audiencia pública", which the prompt asks for), and an entry it marks as
  no error ("This is faithful. No error.", 11 times) no longer fails a
  translation.
- Every translation is made again on version 4 (about 630, about $6),
  newest meetings first; until then a summary is shown in English.

**Still to do.**
- *Most translations fail their review* (the 2026-10-04 audit: about 70%,
  and 408 of 481 summaries shown in English on the Spanish pages). Readers
  get English, which is safe, but most of the Spanish site isn't Spanish.
  Find out whether the review is right; fix the prompt, or the review's
  false flags. Some towns also have few translations because the month's
  budget goes to summaries first (Malden 26 of 157, Manchester 24 of 82, on
  2026-10-05).
- Decision labels matched by content rather than position (a reordered list
  now fails the check entry by entry, so this is belt and braces).
- Ward and District both "Distrito" in Spanish; Beverly's districts are
  lettered, so they read apart, but a town with numbered districts would not.
- `/es/feed.xml` and a Spanish share image: Spanish pages link the English
  feed.
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
- A run that publishes a town without fetching (a push) records it in the
  town's run record too, and rebuilds the status page and the daily issue; a
  fetching run's report names the towns it didn't publish, and fails when it
  published none (v1.30.0, after the status page said "Some data delayed"
  for most of 2026-10-02 about three towns already republished).
- The network status page, [publick.org/status/](https://publick.org/status/),
  built by `scripts/build_status.py` after each run. It's public, so it says
  in plain words which data may be out of date and leaves internals to the
  run's summary.
- One alert a day: `pipeline.network behind` lists towns more than about 30
  hours without a good update and towns whose figure checks keep failing;
  the daily run opens, updates, or closes one issue, assigned to the owner,
  and comments with the towns newly behind (`behind --new-since`), since an
  edit sends no email and a comment does.
- The scheduler Worker (`worker/scheduler.js`) opens, and later closes, a
  "network stopped" issue when no daily run has finished for 30 hours.
- Failures that looked like success (2026-10-03): three SeeClickFix 403s in
  a row (`REFUSED_IN_A_ROW`) are a block, which stops the 311 step without
  marking any request removed (none had been wrongly marked: 0 of 41,718);
  the 311 pages say "Updated" with when SeeClickFix was last read
  (`fetched_at`); a calendar that listed meetings and now lists none counts
  as failed, every run until it lists meetings again.
- *2026-10-05 (#79, released 2026-10-06):* Vermont's graduation rate and
  attendance are expected by October 1 of the year after the school year,
  when the Agency of Education posts them; the engine expected them a year
  early, so Burlington was reported behind from its first day.

**Next.**
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
- Renew the scheduler token before 2027-10-01. When it lapses, runs fall
  back to GitHub's schedule and the "network stopped" check can't open its
  issue.
- Towns queued separately, only if replaced runs turn out to delay towns.

**Matters at:** now, and done enough for Stage 1.

#### Releases

**Why.** The network runs every town on one engine version, so a bad release
breaks every site that uses the broken part. Before 2026-10-02 the engine
released on every merge (22 times in four days), and every move of
`engine-version` checked and republished every page of every town; one
person merged their own pull requests into unprotected branches.

**Done.**
- The engine's tests build Gloucester (CivicPlus calendar), a New Hampshire
  site, and two sample towns (`tests/test_sample_towns.py`): Manchester
  (CivicClerk and DotNetNuke) and Malden (Agenda Center), through the real
  fetchers from saved pages, each with the page, link, and browser checks.
- *Once a day* (v1.30.0, publick.org #38): the engine releases at 08:20 UTC
  the newest tested commit on `main`, with everything merged since, at the
  largest bump its pull requests' labels ask for; a Markdown-only pull
  request is left out without a label. **Actions → Release → Run workflow**
  releases now, for an urgent fix. At 08:40 the network's `engine.yml` opens
  a pull request moving `engine-version`, waits for its run to check every
  page of every town, and merges it if every town passes; the merge
  publishes them. If one fails, nothing moves and the failure emails the
  owner. The network doesn't follow the `v1` tag: the pinned version is what
  rolls back.
- *Started on time* (2026-10-03, #61, publick.org #43): the scheduler Worker
  starts the release and the engine pull request, as it starts the daily
  runs (`RELEASE_CRON`, `ENGINE_CRON`), since GitHub's own schedule started
  neither on the first morning; the workflows keep their schedules as a late
  backup. Both started on time on 2026-10-04 and 2026-10-05.
- *Rulesets* (2026-10-02): the engine's `main` requires a pull request and the
  `test` check, and its `v*.*.*` tags can't be changed or deleted; the
  network's `main` blocks force-pushes and deletion. Engine pull requests
  are merged by a person: the one human look before code reaches every town.
- *A check of each live site after publishing* (#60): a "Check live site"
  step fetches each town's homepage through the Worker until it is the built
  `index.html`, byte for byte, for up to two minutes; a mismatch fails the
  town (`pipeline.deploy check`).
- Accessibility tests in light only, until the sites have dark styles; one
  GitHub backup schedule; the statewide status written only when a source
  was fetched or something changed.

**Next.**
- Sample towns still to add: one with SeeClickFix departments, one on the
  town-website reader (Wallingford), one on Finalsite, one on CivicClerk
  alone (Burlington).
- At 20 to 50 towns: canary towns, Manchester and Malden (chosen
  2026-10-02), take each release a day ahead; `engine-version` moves for the
  rest after a day with the canaries green, by a one-line pull request a bot
  can open, which checks every page of the canaries and a sample of the
  rest. Rolling back is moving `engine-version` back; no town is kept a
  release behind.
- If a push's wait behind a daily run becomes a problem, towns queued
  separately lets a push wait only for its own towns.
- Whether the moving `v1` tag is still needed: the network pins
  `engine-version`, so only a town repository calling `town.yml@v1` uses it.

**Matters at:** now; canaries and sampled checks at about 50 towns.

#### Security and privacy

**Why.** One Worker serves every site, one set of secrets serves every town,
and the 311 data has people's house numbers.

**Done** (from the October review).
- *The open redirect* in the sites Worker, closed and deployed (v1.30.0,
  2026-10-02).
- *Secrets only where needed* (2026-10-03, and #69 and publick.org #46 on
  2026-10-04): each of a town's steps gets only the keys it uses (`STORAGE` in
  `pipeline/update.py`, `keyed` in `pipeline/network.py`, `town.yml` step by
  step), and the network's pull request runs get no keys at all. The steps
  that parse PDFs still hold the document keys, since they read the PDFs
  from the bucket.
- *Supply chain* (#69, publick.org #46): every action in both repositories
  pinned by commit, with its version in a comment; `persist-credentials:
  false` on every checkout but those whose jobs push; boto3 pinned; tag
  protection; Dependabot once a month, grouped, for actions in both and the
  engine's requirements (its first updates merged 2026-10-04 and 10-05).
- *Headers:* every page sets a Content Security Policy in a `<meta>` tag
  (scripts from the site only, no inline scripts). The sites Worker sends
  `Strict-Transport-Security: max-age=31536000` on every page and redirect
  (#69), without `includeSubDomains` or `preload`, so it can be taken back.
  Live since the Worker's deploy of 2026-10-05.
- *311 addresses* (#68): a sensitive category's address is shown to its
  block ("200–299 Main St") on every page, map, CSV, and in the street
  lookup, and its map point to about 100 meters: encampments, health and
  police complaints, noise, problem and private property, smoke detector,
  lost pet, and lead service requests (`seeclickfix.SENSITIVE_CATEGORIES`,
  plus a town's `[seeclickfix] sensitive_categories`). `requests.json` keeps
  SeeClickFix's addresses for the street lookup, and git history isn't
  rewritten (decided 2026-10-03).
- *The privacy line:* the About page says the language switch sets one
  cookie and nothing else.
- *Continuity:* `RUNBOOK.md` in the network repository (publick.org #45):
  each morning's checks, the alerts, rollback, secrets and their expiry. No
  second person with owner access (decided 2026-10-03).

**Next.**
- *Ready for more readers* (2026-10-07). Every request to every site, its
  pages and their CSS, scripts, and images, runs the sites Worker. On
  Workers' free plan, past 100,000 requests a day every site is refused
  until midnight UTC; Workers' paid plan ($5 a month) includes 10 million a
  month. Built on engine `claude/worker-hardening`, deployed with
  **Actions → Worker** once its release reaches `engine-version`:
  - each file kept in Cloudflare's cache in each data center after its
    first read, so a busy page is read from the bucket once per data
    center, not once per visit (files are named by their content's hash, so
    a cached copy is never out of date);
  - a bucket that fails: the manifest last read keeps the site up, and
    otherwise a plain 503 with `Retry-After`, never Cloudflare's error page;
  - `frame-ancestors 'self'` and `X-Frame-Options`, which a `<meta>` policy
    can't set;
  - a rate limit on the digest signup, per IP address and per email address
    (`SIGNUP_LIMITER` in the network's `wrangler.toml`), so the form can't
    be used to send someone confirmation after confirmation.
- *311 requests made private:* a request SeeClickFix stops showing is marked
  removed and left off the pages, but `requests.json` keeps its full
  address, public in git. Drop a removed record's address (from the October
  review's plan).

**Matters at:** now.

#### AI summary and translation costs

**Why.** A per-town limit grew with the number of towns ($5,000 a day at a
thousand). The network has $80 a month for summaries and translations, and
one Anthropic key whose rate limits apply to the whole network.

**Done.**
- The ledger: each town's `data/summary-costs.json`, each month's cost and
  documents, recounted from the saved summaries (which record their cost),
  plus what cut-off responses cost.
- The network budget: the plan job adds up the month across towns and gives
  each town in the run a share of what's left (`pipeline.network budget`),
  $80 every month (publick.org #48). A town stops at its share, or its
  per-run limit ($5 and 50 documents), whichever comes first. Translations
  count in it.
- *A floor per town* (#67): each town keeps `TOWN_FLOOR_SHARE` (half) of its
  even share of each day's budget for every day left in the month; a run's
  towns get what's left beyond the others' floors, so one town's launch
  backlog or busy week can't take the month. The budget step prints each
  day's `floor` and what's `kept_for_floors`.
- The priority order, within each town: upcoming agendas, then documents
  fetched in the last two weeks for a meeting in the last two months, then
  the backlog, which may use what's left beyond a fifth of the budget,
  spread over the month and every town.
- `[summaries] since` (#59): only meetings on or after a date are summarized;
  a new town starts about three months before it launches, since a launch's
  history took most of the money (Beverly, 58% of October's first two days).
- Summaries no longer have the model retype the document (about 60% of the
  cost). A document's full text is laid out free from its PDF for a style
  the engine supports (`pipeline/pdftext.py`, Legistar's to start); a scan
  is transcribed by the model after every summary waiting, from what's
  left; any other PDF has a text layer and the page links it.
- Documents of up to 100 pages are summarized.
- Costs are the maintainer's: never on public pages, left out of exports.

**Spend.** Summaries cost about 2 to 12 cents each; translations about $0.003,
and their review about a cent. September: $21.82 (three towns). October
after six days: $16.88 across ten towns (see
[Where things stand](#where-things-stand-2026-10-07)).

**Next.**
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

- Actions off Node 20, which GitHub has deprecated: Dependabot moved both
  repositories' actions to their newest major versions on 2026-10-04 and
  2026-10-05; check a run's annotations for any warning left.
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
  Wallingford the same day, Lawrence on 2026-10-02, Burlington on 2026-10-04
  (from config alone, on the Vermont package); Lewiston, Bangor, and South
  Kingstown were built the same way (publick.org #58).
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
- New England towns that aren't Census places need building permits found
  by the Census's town (MCD) code (done, `[housing] bps_mcd`, for
  Wallingford); small New Hampshire towns have school districts without a
  high school, so without a graduation rate.
- A town without wards or precincts (Bangor: everyone votes in one place)
  has no area file to place 311 requests in, so no 311 section yet.

**Matters at:** the next town; the helper at about 20 towns.

#### Next towns

Chosen for the people they reach and how little local coverage they have,
not only for ease. A proposal: the order is an open decision.

**Live or in a pull request.** Lawrence (89,000) went live 2026-10-02 in
English and Spanish; its council posts minutes late (5 for 2026 by October),
so its decisions are thin. Burlington, Vermont (45,000) went live 2026-10-04:
CivicClerk with text minutes and SeeClickFix the city answers, where the
Free Press no longer covers City Hall. In publick.org #58, all config only on
v1.36.0:
- *Lewiston, Maine* (37,000): CivicPlus calendar and Archive Center, text
  minutes, a contentious council, no 311. Its School Committee's Google Drive
  uses nested folders, which `[drive_meetings]` can't read yet.
- *Bangor, Maine* (32,000): Agenda Center and calendar; its council and
  School Committee are at-large. Its 311 is off: the city has no wards or
  precincts to place requests in. Some committees post minutes as Word
  files, which the engine doesn't read.
- *South Kingstown, Rhode Island* (32,000): CivicClerk and SeeClickFix, grouped
  by its 11 voting precincts; URI's college town, with an overflowing
  school-cuts meeting in 2026. Its School Committee is on BoardDocs (which
  refused the crawler) and the Secretary of State's portal.

**Massachusetts next** (from a survey of about 65 city and town websites,
2026-10-01; Salem and Medford were already in the works).
1. **Lowell** (115,000, the fourth-largest city). Config only: the City
   Council (30 agendas and 28 minutes in 2026) and School Committee (23 and
   20) in one Agenda Center, minutes with a text layer. Councillors elected
   by district, which the Officials page's wards fit. A large Cambodian
   community (see the languages idea under [Ideas to decide](#ideas-to-decide)).
2. **Springfield** (155,000, the third-largest). CivicClerk, as Manchester:
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

**Elsewhere in New England** (researched 2026-10-01):
- *Vermont:* Montpelier and Winooski (8,000 each, CivicPlus) are config only,
  but have no 311 and may be too small for BLS figures.
- *Maine:* Portland (68,000) is config only (CivicClerk, SeeClickFix), with a
  bigger audience, but 22 boards and its 311 volume weigh on the summary
  budget, and its school board is on BoardDocs.
- *Rhode Island:* Pawtucket (76,000) needs a reader for the Secretary of
  State's Open Meetings portal, where every Rhode Island public body posts
  agendas and must file minutes (text PDFs in every sample): one reader of
  about two days for all 39 municipalities. Providence is too big for now.

Every New England state has a package (2026-10-04), so a new town gets its
state's figures from its first day: its config needs only the package's
keys.

**Matters at:** now.

#### Readers for more platforms

Each reader is written once for every town on the platform, so the order
follows the towns chosen.

| Platform | Reads | Towns |
|---|---|---|
| CivicPlus calendar, month by month, and Archive Center | done (month by month v1.31.0; collections named by year v1.36.0) | Gloucester, Lewiston; Beverly, Lawrence, Malden, Bangor beside their Agenda Centers |
| CivicPlus Agenda Center | done | Malden, Beverly, Lawrence, Bangor; Lowell next |
| CivicClerk | done | Manchester, Burlington, South Kingstown; Springfield, Medford, Portland |
| DotNetNuke | done | Manchester |
| Google Drive folders, one per committee | done | Gloucester's School Committee |
| Google Drive, nested folders | not yet | Lewiston's School Committee |
| Town website file list | done | Wallingford |
| Finalsite district boards, with Google Docs | done (v1.22.0; the district's schedule, v1.31.0) | Wallingford's Board of Education |
| A district's iCalendar feed | done (v1.31.0) | Beverly's School Committee (Edlio) |
| A page of a board's dates | done (v1.31.0) | Malden's School Committee (Finalsite) |
| Rhode Island Open Meetings portal | not yet; about two days | every RI public body, South Kingstown's School Committee |
| Govstack, older Drupal CivicPlus | not yet; about two days each | several surveyed cities |
| Agenda Centers that need JavaScript | not yet | Framingham, Melrose |
| Diligent Community, BoardDocs, ParentSquare, Campus Suite | not yet; small each | school boards (Burlington, Portland, South Kingstown, others) |
| Minutes as Word files | not yet | some of Bangor's committees |

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
packages, were built on 2026-10-04 (#71), before each new state's first town,
from each state's own statewide sources. Each package's docstring lists its
config keys.

**Connecticut, what's left.** The Affordable Housing Appeals List: data.ct.gov
has it through 2023 (`3udy-56vi`), and newer years only as a yearly PDF or
.docx (Wallingford 5.15% in 2025). Wallingford's tax bill and budget are live
from the package since 2026-10-04 (publick.org #51). Connecticut
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
credit. Graduation rates and attendance are expected a year after their
school year, when the Agency of Education posts them (#79).

**Maine, what's left.** The parcel table has values for only about 170 of
Maine's municipalities, sent when each chooses to, so a town's average bill
depends on its own submission being current; Lewiston's adds up to 1.10 times
its 2024 taxable land and buildings. The ESSA Dashboard's export isn't a
published API; the extract also reads the crosstabs downloaded by hand. Test
results start in spring 2023. There's no statewide source for a town's
adopted budget. A town whose parcels carry no land use code (Bangor) gets
its budget page, with the tax rate and what the property tax raises, and no
average bill (v1.36.0).

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
- An audit by hand about weekly while towns are added (decided: it stays
  by hand). The last was 2026-10-02; Burlington hasn't had one.
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
- Also found: most Spanish pages show English summaries, since most
  translations fail their review (see [Spanish quality](#spanish-quality)).

**Done.**
1. The wrong statements fixed (#72): meetings not held, boards whose
   minutes aren't collected, minutes not posted (with where and for how
   long), summaries that won't come, agendas a listing doesn't link, a
   calendar not read lately, a board's first month.
2. The reasons from one module, `pipeline/absences.py` (#73, v1.35.0), from
   what the build knows; each sentence written and translated once. Also: a section page
   says which of its sources are behind; the Officials page says when every
   seat is at-large; the About page lists the sections a town doesn't have
   and why, with `[absences]` for the town's own sentence; every table's –
   has a legend; a saved agenda without a summary says whether one is coming.
3. Site checks fail a page with an unexplained –, an empty Agenda or minutes
   section, minutes "coming" for a meeting not held, an Officials page with
   no ward map and no reason, or an About page missing the sections a town
   lacks.

**Next.**
- `[absences]` sentences for the towns without 311 (Beverly, Lawrence,
  Wallingford, and Lewiston and Bangor once live), from their configs' own
  notes; no town has one yet.
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
A seat can be elected by several wards; a town where every seat is
at-large (Wallingford; Bangor and South Kingstown in #58) says so instead of
showing a map. Every town lists its mayor or equivalent, council, and school
committee.

**Next elections.** Lewiston, Bangor, and South Kingstown on 2026-11-03 (once
live); Burlington on Town Meeting Day, 2027-03-02, with new terms from the
first Monday in April; every other town in November 2027, with new terms in
January 2028. The network README's "Keeping officials current" keeps the
list.

**Next.**
- Each town's next municipal election in its config, and the status page
  flagging a list not checked since. Today the `checked` date is shown and
  never compared with anything (`pipeline/officials.py`).
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
- Summaries translated from the English summary, never the PDF, by Haiku 4.5
  (Sonnet 5.5 since version 4); each translation keyed by the English it
  came from, so turning Spanish on
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
- The network repository's `LICENSE` (2026-10-03, #54, publick.org #42)
  says in plain words what's Publick's (CC BY 4.0, full text in
  `LICENSE-CC-BY-4.0.txt`), how to credit it, and what keeps its own terms
  (311, public records, Census and BLS, state figures, maps); the code is
  MIT. Every About page has "Reusing what's here" with the credit line, and
  every footer on a network site says the summaries and data are free to
  reuse with credit.

**The license (decided 2026-10-01): CC BY 4.0** for what Publick makes: the
summaries, headlines, decision lists, and data compiled from public sources.
The credit asked for: "Summary by Publick (publick.org), AI-generated from
[the source document, linked]". What isn't Publick's keeps its own terms:
311 data stays under SeeClickFix's CC BY-NC-SA 3.0; agendas and minutes are
public records; Census and BLS figures are public domain. Text written
entirely by a model may have little copyright protection in the US, so
credit rests more on custom than on law.

**Next.**
- SeeClickFix's terms, saved as read when the license was written.
- A per-town export, `/data/meetings.json`, in a fixed, documented,
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

**Why.** Collected data is committed daily. A 311 town's data folder is 3 to
15 MB (2026-10-05), most of it 311 requests, much of it rewritten every run. At a thousand
towns that's about 10 GB of working data changing by gigabytes a week, and a
thousand jobs a day pushing to `main` means constant conflicts.

**Done.** Each fetching run records the size of the town's data and what
the run added (`data/run.json`).

**Next.** Keep data files line-stable (sorted keys, one field per line).
311's raw requests to R2 first: planned for before the next 311 town, but
Burlington went live with 311 in git, and South Kingstown (#58) will too, so
before any further one. Then each town's working data, as agenda and minutes
PDFs already are, with git keeping config and code and sites built from the
bucket. Every town has a `[storage]` table. Also: commit each town's data as
soon as it finishes, so a timeout loses nothing (the network's "Commit data"
step doesn't run when a job times out), and give each town a time budget
that ends well before its job's.

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

**Matters at:** tens of towns in one state; SeeClickFix before any further
311 town (four use it now, five with South Kingstown).

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
- The matrix's 256-job limit checked in `plan()` (`pipeline/network.py`).
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
  engine-version              the engine release every town runs, an exact tag (v1.42.0 on 2026-10-06)
  ADDING-A-TOWN.md            the checklist for a new town
  RUNBOOK.md                  what to do when something needs a person
  LICENSE                     CC BY 4.0 for what Publick makes; the code is MIT
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
  .github/workflows/          network.yml (the daily runs, pushes, pull requests), engine.yml
                              (moves engine-version each morning), evaluate.yml (the minutes
                              test set, by hand), worker.yml (deploys both Workers, by hand)
```

The engine reads a town's config, data, and static files from
`PUBLICK_TOWN_DIR`, so each town's commands run unchanged with it set to the
town's folder, each town in its own process (`pipeline/network.py`).

**The daily run.** The `publick-scheduler` Worker starts `network.yml` every
hour from 09:05 to 14:05 UTC; one GitHub schedule, at 12:17 UTC, is a
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
the sites bucket keys, the BLS key, the Cloudflare deploy token,
`ENGINE_PR_TOKEN` (fine-grained, the network repository's Contents and Pull
requests, for the daily engine move), `SCHEDULER_GITHUB_TOKEN`
(fine-grained, the network repository's Actions and Issues and the engine's
Actions, made 2026-09-30 for 366 days, widened 2026-10-03), and the weekly
digest's two Buttondown keys, `BUTTONDOWN_SUBSCRIBE_KEY` (the sites Worker's,
which can't send) and `BUTTONDOWN_SEND_KEY` (the scheduler's). `RUNBOOK.md`
lists when each expires.

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
| v1.34.0 (2026-10-04) | A state package for every New England state: Connecticut's tax bill and budget, Vermont, Maine, and Rhode Island (#71); meeting pages say why minutes or an agenda aren't there, instead of something untrue (#72); five small items: amounts with a scale, keyboard tables, fact check counts in the run record, prompt versions pinned by a test, keys per step (#65). The first release and engine move started by the scheduler |
| v1.35.0 (2026-10-04, by hand) | Decisions anchored to quotes from the minutes, each with its outcome (#66); every page says why something is missing (#73); "Calculated by Publick" notes in Spanish (#74); sitemaps, structured data, and search engine tags for the town sites (#75). The same day: Wallingford's tax bill and budget (publick.org #51) and Burlington (publick.org #54) |
| v1.36.0 (2026-10-05) | A budget floor per town (#67); 311 addresses in sensitive categories cut to the block (#68); HSTS from the Worker, actions pinned by commit, Dependabot (#69); year-named Archive Center collections, Maine budgets without a tax bill, and 311 by voting precinct (#76) |
| v1.37.0 (2026-10-05) | Vermont's graduation and attendance expected a year after the school year (#79); Dependabot's updates (#77, #78) |
| v1.38.0 (2026-10-06) | Spanish summaries by Claude Sonnet 5.5, translated before the backlog and corrected on a second try (#82); docs (#81). Never moved to the towns: the network's check held it (Lewiston's home page, fixed in v1.39.0) |
| v1.39.0 (2026-10-06, moved by hand) | The home page's day column (#83) |
| v1.40.0 (2026-10-06, moved by hand) | At-large wording, Lewiston's School Committee from Google Drive, 311 without areas (#84) |
| v1.41.0 (2026-10-06, moved by hand) | The weekly digest: its pages and feed (#85), and the email's signup through the sites Worker and the Sunday send (#86) |
| v1.42.0 (2026-10-06) | The digest's Cron Trigger with days by name, as Cloudflare takes them (#87) |

The October 2026 outside review (2026-10-02) read both repositories and the
live sites. Its plan, `REVIEW-PLAN.md`, was never merged; every item in it
is done, in the sections above, or was decided otherwise (311 history isn't
rewritten, no person reviews the Spanish, Lawrence launched before Lowell).
The second review the same day is folded in too.

### Where the old numbered items went

Commits and pull requests refer to earlier numbers.

**The October list** (2026-10-02 to 2026-10-04, items 1 to 42):

| Old item | Now |
|---|---|
| 1, 2, 3, 4 (the first scheduled release, the first daily runs, two summaries read by hand) | Done |
| 5. The next meetings audit | Item 8 |
| 6, 8. Decisions anchored to quotes; the test set | Done (#66, v1.35.0) |
| 7, 9, 12, 13, 15, 16 | Done; see their theme sections |
| 10. Security hardening | Done; HSTS live since 2026-10-05 |
| 11. 311 addresses cut to the block | Done (#68, v1.36.0) |
| 14. Push runs that don't publish | Dropped: [Not planned](#not-planned) |
| 17 (first). Every page says why something is missing | Mostly done (#72, #73); the rest is item 11 |
| 17 (second). Lowell | Item 10 |
| 18. Wallingford's figures | Done (publick.org #51); the Appeals List is item 21 |
| 19. A town each in Maine, Vermont, Rhode Island | Done: Burlington (2026-10-04), Bangor, Lewiston, South Kingstown (2026-10-05) |
| 20 to 31 | Items 9, 12 to 20, 22, 23 |
| 32 to 42 | Items 24 to 34 |

**The first list** (to 2026-10-01, items 1 to 18):

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
