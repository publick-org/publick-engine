# Running a town on its own

A town in its own GitHub repository, updated each morning and published on GitHub Pages by the engine's workflow. This is the way to run a site for one town.

## A town in its own repository

This is the stand-alone way to run a town. A town in a network repository has the same files in its folder, `towns/<town>-<state>/`, and the network's own workflow runs it instead of `town.yml`: see the [network's README](https://github.com/publick-org/publick.org#readme).

A town's repository holds:

```
config/<town>.toml            Everything town-specific: name, domain, sources, sections
data/                         Collected data, committed by the daily job
site/static/share/<town>.png  The town's share image (optional; see site.md, Share image)
site/static/...               Any other file that replaces or adds to the engine's site/static/, such as its own favicon.svg
.github/workflows/site.yml    Calls the engine's workflow (below)
```

The engine's [`starter/`](../starter) folder is such a repository, with a config to fill in: copy it to start one. Its workflow calls this repository's, pinned to a version:

```yaml
name: Update and deploy

on:
  push:
    branches: [main]
  pull_request:
  # Daily data refresh, early morning Eastern time.
  schedule:
    - cron: "23 10 * * *"
  workflow_dispatch:
    inputs:
      sources:
        description: Which data to update
        type: choice
        options: [all, meetings, "311"]
        default: all

# One run at a time, so two data commits never race.
concurrency:
  group: update-${{ github.ref }}
  cancel-in-progress: false

jobs:
  site:
    uses: publick-org/publick-engine/.github/workflows/town.yml@v1
    with:
      town: <town>
      sources: ${{ inputs.sources || 'all' }}
    secrets: inherit
    permissions:
      contents: write
      pages: write
      id-token: write
```

The engine's workflow checks out the engine at the same version as the workflow file itself, so the version is set in one place. It fetches new data and commits it (on the schedule or **Run workflow**), builds the site, checks it (`site_checks/`: every page's structure and links, and the WCAG 2.2 AA checks at desktop and phone widths), and deploys it from `main`. Pull requests build and check only.

### Versions

Releases are tagged `v1.0.0`, `v1.1.0` and so on, with a `v1` tag that moves to the newest `v1.x` release. A town pinned to `@v1` takes each compatible release on its next run; one pinned to an exact tag moves when its pin is bumped, which Dependabot can do with a `github-actions` entry in the town's `.github/dependabot.yml`. A change that needs every town's config edited gets a new major version.

A network repository pins every town at once, with an exact tag in one `engine-version` file; publick.org's moves to each new release once every town passes on it (see its [README](https://github.com/publick-org/publick.org#how-it-runs)).

Releasing is automatic, once a day. Each morning at 08:20 UTC, before the network's daily runs, `.github/workflows/release.yml` releases the newest commit on `main` whose engine tests passed, with everything merged since the last release, as the next minor version (`v1.4.0` → `v1.5.0`), and moves `v1` to it. Label a pull request `patch` for a patch version (the release is a patch when every pull request in it is), `major` for a change that needs every town's config edited (`v2.0.0`; towns on `@v1` stay there until they move), or `no release` to leave it out; a pull request that changes only files no town's run reads (Markdown, the tests, the evaluation sets, the linter's settings, and what checks pull requests or makes releases: the ci, evaluate, evaluate-label, public-check and release workflows, `dependabot.yml` and `public_check.py`) is left out without a label. An urgent fix needn't wait: **Actions → Release → Run workflow** releases now. The workflow's own schedule is a backup, which GitHub can start hours late: it does nothing when a run started any other way has succeeded since 08:00 UTC. A release published by hand (**Releases → Draft a new release**) also moves its major tag.

## Deploying

Pushes to the town repository's `main` build, check, and deploy. Pull requests build and check only. The daily schedule and the **Run workflow** button also fetch new data first.

One-time setup, with `<domain>` the site's address (the `domain` in its config) and `<owner>` the GitHub account or organization that owns the repository:

1. **Verify the domain** so no other GitHub account can claim it: **Settings → Pages → Add a domain** on the account or organization that owns the repository, then add the TXT record it gives you at your DNS provider. Verifying a parent domain (e.g. `example.org`) also covers its subdomains, which suits a family of sites like `<town>.example.org`.
2. **Repository settings → Pages → Source:** GitHub Actions.
3. **DNS records** at your DNS provider:
   - For a subdomain such as `gloucester.example.org`: a `CNAME` from it to `<owner>.github.io`.
   - For a bare domain such as `example.org`: `A` records `185.199.108.153`, `185.199.109.153`, `185.199.110.153`, `185.199.111.153`, and a `www` `CNAME` to `<owner>.github.io`.
   - On Cloudflare, set these records to **DNS only** (grey cloud). Proxied records stop GitHub from issuing the site's certificate.
4. **Repository settings → Pages → Custom domain:** enter `<domain>`. Once the certificate is issued, turn on **Enforce HTTPS**.

### Moving a site to a new domain

GitHub Pages serves one custom domain per repository, so when a site moves, its old domain stops working unless it is redirected. One way, with Cloudflare:

1. Create the new domain's DNS records (step 3 above) and wait for them to resolve.
2. Change `domain` in the town's config and merge; then set the same domain under **Repository settings → Pages → Custom domain** straight away. Until then, pages name the new address while being served from the old one.
3. Add the old domain to Cloudflare (**Add a domain**, on any plan) and switch its nameservers at the registrar to the two Cloudflare gives. Before switching, turn off DNSSEC at the registrar if it's on.
4. In the old domain's **DNS → Records**, replace the GitHub records with one `A` record for `@` and one for `www`, both pointing to `192.0.2.1` with **Proxied** (orange cloud). The address is never reached; Cloudflare answers first.
5. **Rules → Redirect Rules → Create rule:** match all incoming requests, with a **Dynamic** redirect to `concat("https://<new domain>", http.request.uri.path)`, status **301**, and **Preserve query string** on. Old links, including deep ones like `/meetings/…`, land on the same page at the new address.

If nobody relies on the old domain, deleting its GitHub records is enough; don't leave them pointing at GitHub Pages with no repository claiming the domain.

## Email

`contact_email` can be any address. A forwarding address on the site's own domain keeps a personal inbox private and lets each town's mail be sorted or handed to someone else later. Cloudflare **Email Routing** does this for free: **Email Routing → Routing rules → Create address**, action **Send to an email**. It only receives mail; replies go out from the destination inbox.

## Secrets

Set these under the repository's **Settings → Secrets and variables → Actions**. With several town repositories in one GitHub organization, set them once as **organization** secrets instead, so every town's workflow gets them; a repository secret of the same name overrides it.

- `ANTHROPIC_API_KEY` (optional): enables agenda and minutes text and summaries. Without it the step is skipped.
- `STORAGE_ACCESS_KEY_ID`, `STORAGE_SECRET_ACCESS_KEY` (needed with `[storage]`): an R2 API token for the documents bucket. See [Document storage](storage.md#document-storage).
- `SITES_ENDPOINT`, `SITES_BUCKET`, `SITES_ACCESS_KEY_ID`, `SITES_SECRET_ACCESS_KEY` (needed to publish to the sites bucket): see [Serving many sites from one bucket](network.md#serving-many-sites-from-one-bucket).
- `BLS_API_KEY` (optional): free key from bls.gov/developers for the unemployment rate. Without it the job uses BLS's keyless limit, then falls back to the bulk data file.
- `PRIVATE_ENDPOINT`, `PRIVATE_BUCKET`, `PRIVATE_ACCESS_KEY_ID`, `PRIVATE_SECRET_ACCESS_KEY` (optional): the private store, where the summaries' costs are kept out of git. A network sets `SUMMARY_BUDGET` and `SUMMARY_BUDGET_MONTH` too. See [Cost records](storage.md#cost-records).

A network served by the Workers also sets `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (to deploy them), and for the weekly digest `BUTTONDOWN_SUBSCRIBE_KEY` and `BUTTONDOWN_SEND_KEY` (see [The email](site.md#the-email)).
