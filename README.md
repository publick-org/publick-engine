# Publick engine

The code behind independent, read-only websites that publish public data about a
town: what's on upcoming meeting agendas and what was decided, how the city
answers 311 requests, the budget and tax bill, schools, housing, and who
represents you. It runs the [Publick network](https://publick.org)'s sites, such
as [Gloucester Publick](https://gloucester-ma.publick.org) and
[Manchester Publick](https://manchester-nh.publick.org), and it's MIT-licensed so
anyone can run a site like this for their own town.

Each site is static HTML, built by a Python pipeline that fetches the town's data
each morning in GitHub Actions. There is no server and no database.

## What a site has

- **Meetings:** every board's upcoming meetings, agendas and minutes, with
  AI-written summaries and the decisions made, labeled as AI-written; a summary
  from the meeting's recording the next day, where it's recorded; roll call
  votes, for minutes in a format the engine reads.
- **311:** how quickly the city answers requests, by category and by ward.
- **Budget and tax bill, schools, housing, and unemployment,** from state and
  federal sources.
- **Officials:** who represents each part of the town, and when their terms end.
- **A weekly digest** of the week's meetings and decisions, as a page and a feed.
- **Spanish** alongside English, an open data export, and pages that meet
  WCAG 2.2 AA, checked on every build.

## Where it works

The engine is built for US towns. What a site can show depends on where the
town publishes its data:

| Section | Needs | Where |
|---|---|---|
| Meetings | The town's calendar or documents on a platform the engine reads: CivicPlus, CivicClerk, Legistar, PrimeGov, DotNetNuke, Google Drive folders, an iCalendar feed, Finalsite, Diligent Community, BoardBook, and others ([Meeting sources](docs/meetings.md)) | Anywhere |
| AI summaries | An [Anthropic API key](https://console.anthropic.com) (optional; without one, meetings are listed without summaries) | Anywhere |
| Recordings | Meetings recorded on TelVue, Cablecast, Castus, CivicClerk, the Internet Archive, or YouTube | Anywhere |
| 311 | Requests on SeeClickFix, or a Socrata open data portal | Anywhere |
| Housing, unemployment | Census and Bureau of Labor Statistics data | Anywhere |
| Officials | Entered by hand, from the town's website | Anywhere |
| Budget, tax bill, schools | A package for the state in `pipeline/states/` | Massachusetts, New Hampshire, Connecticut, Vermont, Maine, Rhode Island |

A town in another state gets every section but the budget and schools. Adding a
state is one package and its pages ([State figures](docs/states.md)).

## Try it

All you need is Python 3.12 or newer: no accounts or keys. This builds the
sample town, Gloucester, Massachusetts, from saved data:

```sh
git clone https://github.com/publick-org/publick-engine
cd publick-engine
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
cp -r tests/fixtures/town ../my-town
cd ../my-town
PYTHONPATH=../publick-engine python -m pipeline.build_site --no-share-cards
python -m http.server -d _site 8000
```

Then open http://localhost:8000. The first build downloads the sample's 311 data
from the Publick network's public file host.

## Start a site for your town

1. **Copy the starter.** From the engine's folder:

   ```sh
   cp -r starter ../yourtown
   ```

   It's a town's repository with nothing in it yet: a config to fill in,
   `config/town.toml`, and the workflow that publishes the site.
2. **Fill in the config.** Each `# To fill in:` comment in it is something to
   do: the site's name and address, the town, and a section for each source
   you find, starting with meetings ([Meeting sources](docs/meetings.md)).
   [Configuring a town](docs/configuration.md) goes through every table, and
   the sample's `tests/fixtures/town/config/gloucester.toml` shows a town with
   every section. The site checks don't pass, so the site isn't published,
   while any is left. Pick a name of your own: the Publick name and "P" icon
   are for the Publick network's sites.
3. **Fetch your town's data and build**, from `yourtown/`:

   ```sh
   export PYTHONPATH=../publick-engine
   python -m pipeline.fetch_meetings && python -m pipeline.fetch_minutes
   python -m pipeline.build_site --no-share-cards && python -m http.server -d _site 8000
   ```

   [Configuring a town](docs/configuration.md#starting-a-site-for-another-town)
   lists the command for each other source.
4. **Publish it** from a GitHub repository of its own, updated each morning and
   served free on GitHub Pages: [Running a town on its own](docs/stand-alone.md).

If a step doesn't work for your town, please
[open an issue](https://github.com/publick-org/publick-engine/issues): these
instructions improve with each town that tries them.

## Documentation

- [Configuring a town](docs/configuration.md): the config file, and every data source's table
- [Meeting sources](docs/meetings.md): each calendar and portal the engine reads, and its settings
- [State figures](docs/states.md): budgets, tax bills and schools, state by state
- [Running a town on its own](docs/stand-alone.md): the town's repository, its workflow, versions, deploying, and secrets
- [Documents and cost records](docs/storage.md): keeping PDFs and large data files out of git
- [What a site has](docs/site.md): languages, meeting pages, the weekly digest, accessibility, open data
- [Running a network of towns](docs/network.md): many towns from one repository, served by one Worker, as the Publick network runs
- [Working on the engine](docs/development.md): the code's layout, local builds and tests, and adding a section

## Data and licenses

The code is under the [MIT License](LICENSE). Data keeps the terms of its source; each town's `data/README.md` lists its sources (`python -m pipeline.data_readme` writes it from the town's config and data folder). On the Publick network, what Publick makes (summaries, headlines, decision lists, compiled data) is under CC BY 4.0, as the network repository's [`LICENSE`](https://github.com/publick-org/publick.org/blob/main/LICENSE) says. 311 data comes from [SeeClickFix](https://seeclickfix.com) under [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/), or from a city's open data portal under its own license. Other sources are listed on the site's [About page](https://gloucester-ma.publick.org/about/).
