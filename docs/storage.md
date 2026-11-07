# Documents and cost records

Where agenda and minutes PDFs, a 311 town's large data files, and the AI summaries' cost records are kept, so the repository stays small and its data public.

## Document storage

Agenda and minutes PDFs average well over a megabyte, and git keeps every version forever, so a repository that holds them only grows: one holding every town's, as a network's does, would soon be too large to clone quickly (and a GitHub Pages site may be at most 1 GB). Without a `[storage]` table they're committed under `data/meetings/` and copied into the site, which works for a small or short-lived town. With one, they go to an S3-compatible bucket and pages link to the bucket's public address. Git keeps each document's text, summary and SHA-256 hash, so the site is still rebuilt entirely from the repository.

A 311 town with a `[storage]` table also keeps its `data/311/requests.json` and `scorecard.json` in the bucket, under `<prefix>/data/` (`pipeline/bucket_data.py`), since each daily run rewrites much of them: in git, 311 was four fifths of what a network's repository grew by. A step that reads them (the 311 steps, the build, the freshness check) downloads each one missing from `data/` from the bucket's public address, with no keys, so a pull request's build or a local one shows the latest; `fetch_311` and `compute_311` save them back, with the keys. `fetch_311` reads the bucket's copy first, and stops rather than start again from nothing when the town's `311/status.json` says it had requests. Where its sync has got to, `311/sync.json`, stays in git with `status.json`, and is written only after `requests.json` is saved to the bucket, so it's never ahead of it. A network repository then ignores the two files in git (its `.gitignore`), once a daily run on this engine has saved them to the bucket. They're public, as they were in git.

One bucket can serve several towns, each under its own `prefix`. Cloudflare R2 is the suggested host: no charge for downloads, and the first 10 GB are free. Steps 1–3 are done once per bucket; each town then needs step 4, and step 5 if it already has PDFs in git.

1. **Create the bucket** in Cloudflare: **R2 object storage → Create bucket**.
2. **Give it a public address:** the bucket's **Settings → Custom Domains → Add**, e.g. `files.example.org`. The domain's DNS must be on Cloudflare, in the same account. The bucket's `r2.dev` address is rate-limited and meant only for testing.
3. **Create an API token:** **R2 object storage → API Tokens → Manage → Create Account API token**, with *Object Read & Write* on that bucket only. Save its access key ID and secret as the secrets `STORAGE_ACCESS_KEY_ID` and `STORAGE_SECRET_ACCESS_KEY`.
4. **Add the table** to `config/<town>.toml`, with the account ID from the R2 overview page:

   ```toml
   [storage]
   endpoint = "https://<account id>.r2.cloudflarestorage.com"
   bucket = "<bucket name>"
   public_url = "https://files.example.org"
   prefix = "<town>"   # optional; defaults to the config file's name
   ```

   The default is the config file's name (`gloucester`), so a town in a network sets its folder's name, `<town>-<state>`, to keep two towns' files apart.

5. **Run the workflow.** New PDFs go straight to the bucket. The **Move saved documents to storage** step uploads the ones already in `data/meetings/`, checks each copy, and commits their removal. Until a file is moved, the site keeps linking to its copy in the repository.

The files remain in the repository's git history. Shrinking the history means rewriting it, which is a separate decision.

## Cost records

What each request to the model costs is written to a log as it's answered, and each month's totals are made from it (`pipeline/cost_log.py`); a network's daily runs share one monthly budget by those totals. A repository whose data is public shouldn't publish them, so they're kept out of git, and the records saved in a town's data folder don't carry them: a summary, transcription, translation, or batch of drafts keeps its model, prompt version, and time, but not its cost or token counts, and one saved before with them loses them when it's saved again (`cost_log.without_costs`), as does a recording's transcript in `data/meetings/recordings.json`:

- **With a private store**: an S3-compatible bucket that nothing serves, set by the secrets `PRIVATE_ENDPOINT`, `PRIVATE_BUCKET`, `PRIVATE_ACCESS_KEY_ID` and `PRIVATE_SECRET_ACCESS_KEY`. Each town's costs are under `costs/<town>/` (its `[storage]` prefix, or its config's name): one object for each run's lines in a month, and the ledger, `summary-costs.json`. Each line is appended to a copy on disk (`data/costs/pending/`, flushed) and then uploaded, so a run stopped at any moment, even with its machine, has counted every request but the one in flight; a copy that couldn't be uploaded goes up with the job's next step.
- **Without one**: the same lines in `data/costs/`, whose own `.gitignore` keeps them out of git, and the ledger in `data/summary-costs.json`, as before. A town in its own repository that doesn't want the ledger in git adds `data/summary-costs.json` to its `.gitignore` (its runs then start each month's log again, from the summary files, which don't record what they cost: the month's row counts only what that run's machine has logged).

The first run with the private store's keys moves a town's existing `data/summary-costs.json` into the store (the network's budget step imports every town's before any town runs), starts the month's log from it, and deletes the file, so the run's data commit takes it out of git. Its earlier versions stay in the git history.

The network's monthly budget is given as the secrets `SUMMARY_BUDGET` (dollars a month) and `SUMMARY_BUDGET_MONTH` (`YYYY-MM=dollars` for a month with its own, several separated by commas), so the run logs never show it: GitHub hides a secret's value in a log. `python -m pipeline.network budget` prints each town's share for the run as a fraction of the month's budget, and the budget's use as a fraction; the summaries step makes the fraction dollars again from the same secrets. No step prints what anything cost. The minutes and translation sets (`pipeline/evaluate.py`) leave the costs out of their reports and JSON when `PUBLICK_HIDE_COSTS` is set, as a workflow whose log is public sets it.

One-time setup, with Cloudflare R2:

1. **Create a bucket** (**R2 object storage → Create bucket**), separate from the documents and sites buckets. Give it no custom domain and leave its `r2.dev` address off, and bind it to no Worker: nothing should serve it.
2. **Create an API token** (**R2 object storage → API Tokens → Manage → Create Account API token**) with *Object Read & Write* on that bucket only.
3. **Set the secrets** `PRIVATE_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`), `PRIVATE_BUCKET`, and the token's `PRIVATE_ACCESS_KEY_ID` and `PRIVATE_SECRET_ACCESS_KEY`; for a network, `SUMMARY_BUDGET` (and `SUMMARY_BUDGET_MONTH` when a month has its own).
