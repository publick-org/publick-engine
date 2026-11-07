# Running a network of towns

Many towns run from one repository, sharing settings and served from one bucket by a Cloudflare Worker. This is how the Publick network runs, and its repository, [publick-org/publick.org](https://github.com/publick-org/publick.org), is a working example. A single town doesn't need it: see [Running a town on its own](stand-alone.md).

## Settings shared by a network's towns

In a network repository, what many towns have in common is written once, in two optional files at the network's root, and each town's config keeps only what is its own:

```
defaults/network.toml       What every town shares: [summaries] model, effort and prices, the documents bucket
                            ([storage]), GoatCounter ([analytics]), the footer's network name, 311 and digest settings
defaults/states/<st>.toml   What every town in one state shares, picked by the town's [town] state_abbr ("MA" is
                            defaults/states/ma.toml): the state's name, its Census and BLS codes, its laws' terms
```

A town's own file wins over its state's, and its state's over the network's (`pipeline/config.py`):

- **Tables merge key by key**, nested ones too; any other value, an array included (`languages`), is replaced whole. Arrays of tables (`[[sections]]`, `[[officials.bodies]]`, `[[glossary]]`, `[freshness] sources`) are each town's alone: a layer can't set them.
- **A layer fills in a table only when the town's own file has it**, since a table turns its source on: the network's `[seeclickfix]` settings complete each 311 town's own `[seeclickfix]` (its `organization_id` and `report_url`) and never give 311 to a town without one, and an empty `[digest]` takes the network's email signup. `[terms]` and `[strings]` go to every town: they're wording looked up by key.
- **`[terms]`** holds the glossary by English term (`pipeline/glossary.py`). A term given as a table has a column for each language, its wording and its definition there: `[terms."warrant article"]` with `en = { definition = "..." }` and `es = { term = "artículo de la convocatoria (warrant article)", definition = "..." }` (and, for a term written other ways, `also`, a list, or `pattern`, a regular expression). Another language is another column. A term given so is in the glossary of every town that takes the layer: the town's glossary page (`/meetings/glossary/`, in each language), the "Terms used here" on its meeting pages, a link from each term's first use on a meeting page or the decisions page, and the translator's words. A term given as a string is a definition alone, for a `[[glossary]]` entry to take, with its translation in the same layer's `[strings.es]`. A town's `[[glossary]]` entry with no `definition`, only its `term` (and `bodies`, if any), takes the definition from its state's or the network's `[terms]`; an entry with its own `definition` keeps it, and wins over the layers' term of the same name.
- A town can't remove a key a layer sets; it sets its own value.
- **Checked when loaded:** a layer may set only the keys in `LAYER_KEYS` (`pipeline/config.py`), with their types, and anything else in a layer file stops the load with the file and the key ("defaults/network.toml: [summaries] modle isn't a setting a layer can have; did you mean model?"). A town's file may have only the tables the engine reads (`TABLES`), and the keys layers carry are type-checked in a town's file too. Each module still checks its own table's keys.

A town in its own repository has no layers and is read as before. A town folder outside the network's `towns/` (a copy) finds them through `PUBLICK_NETWORK_DIR`. To see a town's config as the engine does, each value with the file it came from:

```sh
PUBLICK_TOWN_DIR=towns/<town>-<state> python -m pipeline.config show
```

A change to `defaults/network.toml` rebuilds every town in the network's run, and one to `defaults/states/<st>.toml` that state's towns (`pipeline/network.py`, `changed_towns`).

## Serving many sites from one bucket

GitHub Pages serves one custom domain per repository. A network that runs many towns from one repository publishes each built site to a Cloudflare R2 bucket instead, and one Cloudflare Worker (`worker/index.js`) serves them all, choosing the site by hostname:

```sh
python -m pipeline.deploy publish [--town <town>] [--site _site] [--domain <domain>]   # the site goes live at its config's domain
python -m pipeline.deploy rollback [--town <town>] [--build <build>] [--domain <domain>] # back to the previous build, or a named one
python -m pipeline.deploy prune [--keep 10] [--dry-run]              # delete old builds and unused files
python -m pipeline.deploy check [--town <town>] [--site _site] [--domain <domain>]  # wait until the live homepage is the build just published
```

`--domain` names the site by its address instead of by its town's config, as the network's rollback does. The build's moved pages (`redirects.json`: a meeting's or board's old address, to its page now) go in the manifest rather than the bucket, and the Worker answers each with a 301, cached for five minutes like a page, so a rollback takes effect as quickly; publishing refuses a redirect to a page the build doesn't have. Files are stored once by content and shared across sites, so a daily publish uploads only what changed. A site goes live with one write, after all its files are uploaded. See `pipeline/deploy.py` for the bucket layout.

Under load, the Worker keeps each file's content in Cloudflare's cache in each data center after its first read (files are named by their content's hash, so a cached copy is never out of date), so a busy page is read from the bucket once per data center, not once per visit. If the bucket can't be read, a site keeps being served from the manifest last read, and otherwise the visitor gets a plain 503 with `Retry-After`, never Cloudflare's error page. Every page is sent with HSTS, `nosniff`, and `frame-ancestors 'self'` (no other site may frame it), which the pages' own `<meta>` Content-Security-Policy can't set. Each request runs the Worker, pages and their files alike, across every site, so the account's Workers plan must allow the network's daily requests.

GitHub starts scheduled workflows when it can, sometimes hours late. A network can start its daily runs on time with a second Worker, `worker/scheduler-index.js`: on each Cron Trigger it starts the network workflow through GitHub's API (a daily run, which takes only the towns that are due, so extra starts do nothing), and it opens an issue if the status page shows no daily run has finished for 30 hours. Two more triggers (`RELEASE_CRON`, `ENGINE_CRON`) start this engine's release and the network's engine pull request each morning (so the token also needs Actions read and write on the engine's repository), `DIGEST_CRON` sends the weekly digest (see [The email](site.md#the-email)), and `UPTIME_CRON` (`50 * * * *`) loads every site's homepage, publick.org's and each town's in its sitemap, through the sites Worker, and opens a "site down" issue for any that fails twice, closing it when all load again. It needs a GitHub token that can start the workflow and open issues, and the account needs a `workers.dev` subdomain for Cron Triggers, even though the Worker has no address of its own. See `worker/scheduler.js`, and the network repository's `wrangler.scheduler.toml`.

Setup, once for the network:

1. **Create a bucket** for sites (**R2 object storage → Create bucket**), separate from the documents bucket and with no public address: only the Worker reads it.
2. **Create an API token** with *Object Read & Write* on that bucket only. Set the secrets `SITES_ACCESS_KEY_ID` and `SITES_SECRET_ACCESS_KEY`, and `SITES_ENDPOINT` (`https://<account id>.r2.cloudflarestorage.com`) and `SITES_BUCKET`.
3. **Deploy the Worker** from `worker/index.js` with an R2 binding named `SITES` to that bucket.
4. **Route the sites to it:** a proxied (orange cloud) DNS record for each site's hostname, or one wildcard such as `*.example.org`, and a Worker route such as `*.example.org/*`. A site's specific DNS record takes precedence over the wildcard, so delete a town's GitHub Pages `CNAME` to move it to the Worker.

## Address alerts

A town with `[alerts] follow = true` has a follow form on its street lookup, which the sites Worker answers (`worker/alerts.js`), keeping each follow in a D1 database and sending email through Postmark. Setup, once for the network:

1. **Create the database:** `npx wrangler d1 create <name>`, then apply the schema: `npx wrangler d1 execute <name> --remote --file worker/alerts.sql` (each statement can be run again). Bind it to the sites Worker as `ALERTS_DB`.
2. **Set up the email service:** a Postmark server, a sending domain with its DKIM and Return-Path records, and the server's token as the sites Worker's secret `POSTMARK_TOKEN`. Set `ALERTS_FROM`: the sending address (`alerts@your.domain`), or the domain alone (`@your.domain`) for an address for each town there, its name without the state (`salem@your.domain` for salem-ma). Each email is from its site's name at it, and optionally `ALERTS_STREAM` (the message stream, `outbound` by default) and `ALERTS_MAILING_ADDRESS` (the postal address in each email's footer).
3. **Set two secrets** on the sites Worker, each a long random string: `ALERTS_TOKEN_SECRET`, which makes each reader's link to their own page and their unsubscribe link, and `ALERTS_HOOK_SECRET`, which the network's run sends to start a town's alerts.
4. **Send after each publish:** after a town's site is published, post to `https://<the town's host>/alerts/send` with `Authorization: Bearer <ALERTS_HOOK_SECRET>`. The Worker reads the site's `/streets/mentions.json`, emails each reader the mentions posted since the town's last send, and answers with what it sent; a send already made that day for the town waits for the next day's.

The first send for a town starts from that moment: mentions already listed aren't sent. A reader who unsubscribes, or stops their last alert, is deleted with everything about them, as is one Postmark won't send to again (a hard bounce or a spam complaint, its error 406). Changing `ALERTS_TOKEN_SECRET` changes every reader's links, so the old ones stop working.

