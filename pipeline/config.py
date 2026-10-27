"""Load per-town configuration from config/<town>.toml.

The engine (this repository) holds the code, page templates, and shared static
files. Each town has its own repository with its config/, its data/, and
optionally site/static/ files that replace or add to the engine's (its share
image, or its own icon). Commands run from the town's repository, or with
PUBLICK_TOWN_DIR pointing at it.

A town in a network repository (towns/<town>-<state>/ in it, or any folder with
PUBLICK_NETWORK_DIR naming the network) also takes the settings its towns share,
from two optional files in the network's defaults/ folder:

  defaults/network.toml       what every town in the network shares: [summaries] model, effort and
                              prices, the documents bucket, the footer's network name
  defaults/states/<st>.toml   what every town in one state shares, picked by the town's [town] state_abbr:
                              the state's name, its Census and BLS codes, the wording of its laws

The town's own file wins over its state's, and its state's over the network's.
Tables merge key by key; any other value, an array included, is replaced whole.
A layer fills in a table only when the town's own file has that table, since a
table turns its source on ([seeclickfix] is 311, [digest] the email signup): the
network's [seeclickfix] settings never give 311 to a town without it. [terms]
and [strings] are the exceptions, merged into every town, since they are
wording looked up by key. A town can't remove a key a layer sets; it sets its
own value. Without either file (a town in its own repository) a config is read
as it always was.

[terms] holds the glossary by English term. A term's value is either its English
definition alone, or a table with a column for each language (en, es, ...),
each an inline table: the term's wording in that language (term; English's is
the key), its definition, and, if it's written other ways, also (a list) or
pattern (a regular expression) to find it by. A term given as a table is in
every town's glossary (pipeline/glossary.py): its page, the links from meeting
pages, and the translator's words. Another language is another column:

  [terms."warrant article"]
  en = { definition = "One item on the warrant, the list of what a town meeting will vote on." }
  es = { term = "artículo de la convocatoria (warrant article)", definition = "Un asunto de la convocatoria..." }

A town's [[glossary]] entry with no definition, only its term (and bodies, if
any), takes the definition from [terms], so a state's wording of its laws is
written once; an entry with its own definition keeps it, and wins over a term
of the same name in [terms] (a word that means something else in that town).

What a layer may set is LAYER_KEYS, with each value's type (or, for an effort,
the values it may take): anything else in a layer file stops the load, naming
the file and the key. A town's file may have
only the tables in TABLES, and the keys layers carry are checked for their type
wherever they're set. Each module still checks its own table (load_config).

    python -m pipeline.config show [--town <town>]   # the merged config, each value with the file it came from
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import sys
import tomllib
from datetime import date, datetime, time
from pathlib import Path

ENGINE_DIR = Path(__file__).resolve().parent.parent
TOWN_DIR = Path(os.environ.get("PUBLICK_TOWN_DIR") or Path.cwd()).resolve()
CONFIG_DIR = TOWN_DIR / "config"
DATA_DIR = TOWN_DIR / "data"
# Files that replace or add to the engine's site/static/ for this town.
TOWN_STATIC_DIR = TOWN_DIR / "site" / "static"
# The network repository a town's folder is in, when it isn't towns/<town>-<state>/ in it (a copy of the folder).
NETWORK_ENV = "PUBLICK_NETWORK_DIR"
# The network's shared settings, in its repository: defaults/network.toml, defaults/states/<st>.toml.
LAYERS = "defaults"

# Every table a town's config may have (the README's table of them, under "Starting a site for another town").
TABLES = {"site", "town", "sections", "analytics", "digest", "meetings", "archive", "schedule_meetings", "ical_meetings",
          "finalsite_meetings", "drive_meetings", "page_meetings", "diligent_meetings", "opengov_meetings", "meetingdocs_meetings",
          "seeclickfix", "socrata_311",
          "storage", "summaries", "participation", "glossary", "terms", "schools", "finance", "housing", "labor",
          "permits", "freshness", "officials", "absences", "strings"}
# Merged into every town, not only one whose own file has the table: wording looked up by key.
EVERY_TOWN = ("terms", "strings")
# A number: an integer or a decimal, as TOML writes either.
NUMBER = "number"
ANY = "*"
# An effort level the API takes (output_config.effort). A tuple of strings in the schema is a choice of one of them.
EFFORT = ("low", "medium", "high", "xhigh", "max")
# A glossary term ([terms]): its English definition, or a table with a column for each language (term_problem()).
TERM = "term"
# What a language's column of a term may have, and each value's type.
TERM_KEYS = {"term": str, "definition": str, "also": [str], "pattern": str}
# What a layer may set, and each value's type. A table nests; ANY stands for any key (a term, a language).
LAYER_KEYS = {
    "site": {"network": str, "network_url": str, "repo_url": str, "name_suffix": str, "timezone": str,
             "languages": [str]},
    "town": {"state": str},
    "analytics": {"goatcounter": str, "public_stats": str},
    "storage": {"endpoint": str, "bucket": str, "public_url": str},
    "summaries": {"model": str, "effort": EFFORT, "input_price": NUMBER, "output_price": NUMBER,
                  "long_prompt_tokens": int, "long_input_price": NUMBER, "long_output_price": NUMBER,
                  "max_per_run": int, "max_cost_per_run": NUMBER, "translation_model": str,
                  "translation_input_price": NUMBER, "translation_output_price": NUMBER, "translation_effort": EFFORT,
                  "translation_review_model": str, "translation_review_input_price": NUMBER,
                  "translation_review_output_price": NUMBER, "translation_review_effort": EFFORT},
    "freshness": {"summary_grace_days": NUMBER, "grace_months": NUMBER},
    "digest": {"signup": bool, "provider": str, "privacy_url": str},
    "seeclickfix": {"open311_base": str, "api_base": str, "request_delay": NUMBER, "recent_days": int,
                    "max_details_per_run": int, "time_budget_seconds": NUMBER, "catch_up_requests": int,
                    "listing_delay": NUMBER, "listing_refused_hours": NUMBER,
                    "repeats": {"radius_m": NUMBER, "window_days": int, "recent_open_days": int}},
    "housing": {"bps_region": str, "bps_prefix": str, "bps_state": str, "state_geo": str, "shi_url": str},
    "labor": {"state_series": str, "bulk_file": str},
    "schools": {"portal": str},
    "terms": {ANY: TERM},
    "strings": {ANY: {ANY: str}},
}


def only_town() -> str:
    """The town's config name when its repository has exactly one config file."""
    towns = sorted(CONFIG_DIR.glob("*.toml")) if CONFIG_DIR.is_dir() else []
    return towns[0].stem if len(towns) == 1 else ""


# The town every command uses unless given --town. The workflow sets TOWN once.
DEFAULT_TOWN = os.environ.get("TOWN") or only_town()


def network_dir(town_dir: Path) -> Path | None:
    """The network repository a town's folder is in: PUBLICK_NETWORK_DIR, or the folder above towns/
    when the town is towns/<town>-<state>/. None for a town in its own repository."""
    if os.environ.get(NETWORK_ENV):
        return Path(os.environ[NETWORK_ENV]).resolve()
    town_dir = town_dir.resolve()
    return town_dir.parent.parent if town_dir.parent.name == "towns" else None


NETWORK_DIR = network_dir(TOWN_DIR)


def shown(path: Path) -> str:
    """A file's path as a message names it: from the current folder when it's under it."""
    try:
        return str(path.resolve().relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def layer_files(network: Path | None, state_abbr: str | None) -> list[Path]:
    """The layer files a town takes, the nearest first: its state's, then the network's. Each only if it exists."""
    if not network:
        return []
    base = network / LAYERS
    files = ([base / "states" / f"{state_abbr.lower()}.toml"] if state_abbr else []) + [base / "network.toml"]
    return [f for f in files if f.is_file()]


def read_toml(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise SystemExit(f"{shown(path)}: {e}") from None


def key_name(path: tuple) -> str:
    """How a message names a key: [summaries] model, [seeclickfix.repeats] radius_m, [terms] "M.G.L."."""
    def bare(key) -> str:
        return key if re.fullmatch(r"[A-Za-z0-9_-]+", str(key)) else json.dumps(key, ensure_ascii=False)
    return f"[{'.'.join(map(bare, path))}]" if len(path) == 1 else f"[{'.'.join(map(bare, path[:-1]))}] {bare(path[-1])}"


def term_problem(value) -> str | None:
    """What's wrong with a [terms] value, or None: a string (the English definition), or a table of
    languages, each an inline table of TERM_KEYS, English's with a definition and every other's with
    its term and definition. A pattern must compile."""
    if isinstance(value, str):
        return None
    if not isinstance(value, dict) or not value:
        return "a string (its definition) or a table of languages"
    if "en" not in value:
        return "a table with an en column, its English definition"
    for lang, column in value.items():
        if not re.fullmatch(r"[a-z]{2}", lang):
            return f"a table of languages by their two-letter code, not {lang!r}"
        if not isinstance(column, dict):
            return f"a table of languages, each an inline table ({lang} is {column!r})"
        for key, v in column.items():
            if key not in TERM_KEYS:
                return f"a table of languages, each with {', '.join(TERM_KEYS)} ({lang} has {key!r})"
            if not fits(v, TERM_KEYS[key]):
                return f"a table whose {lang}.{key} is {type_name(TERM_KEYS[key])}"
        needs = ("definition",) if lang == "en" else ("term", "definition")
        for key in needs:
            if not column.get(key):
                return f"a table whose {lang} column has its {key}"
        if column.get("pattern"):
            try:
                re.compile(column["pattern"])
            except re.error as e:
                return f"a table whose {lang}.pattern is a regular expression ({e})"
    return None


def type_name(spec) -> str:
    if spec == TERM:
        return "a string (its definition) or a table of languages"
    if isinstance(spec, tuple):
        return "one of " + ", ".join(json.dumps(v) for v in spec)
    return ("a table" if isinstance(spec, dict) else "a list of strings" if isinstance(spec, list)
            else "a number" if spec == NUMBER else {str: "a string", int: "a whole number", bool: "true or false"}[spec])


def fits(value, spec) -> bool:
    if spec == TERM:
        return term_problem(value) is None
    if isinstance(spec, tuple):
        return value in spec
    if isinstance(spec, list):
        return isinstance(value, list) and all(fits(v, spec[0]) for v in value)
    if spec == NUMBER:
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if spec is int:
        return isinstance(value, int) and not isinstance(value, bool)
    return isinstance(value, spec)


def check_layer(layer: dict, where: str, schema: dict = LAYER_KEYS, path: tuple = ()) -> None:
    """Stop at a key a layer can't set, or a value of the wrong type, naming the file and the key."""
    for key, value in layer.items():
        here = path + (key,)
        spec = schema.get(key, schema.get(ANY))
        if spec is None:
            known = [k for k in schema if k != ANY]
            close = difflib.get_close_matches(key, known, 1)
            raise SystemExit(f"{where}: {key_name(here)} isn't a setting a layer can have"
                             + (f"; did you mean {close[0]}?" if close else
                                f". A layer can set {', '.join(key_name(path + (k,)) for k in known)}; anything "
                                "else is each town's own (pipeline/config.py, LAYER_KEYS)."))
        if isinstance(spec, dict) and isinstance(value, dict):
            check_layer(value, where, spec, here)
        elif spec == TERM and term_problem(value):
            raise SystemExit(f"{where}: {key_name(here)} should be {term_problem(value)}.")
        elif not fits(value, spec):
            raise SystemExit(f"{where}: {key_name(here)} should be {type_name(spec)}, not {value!r}.")


def check_town(own: dict, where: str) -> None:
    """Stop at a table no part of the engine reads, most likely a misspelling."""
    for table in own:
        if table not in TABLES:
            close = difflib.get_close_matches(table, sorted(TABLES), 1)
            raise SystemExit(f"{where}: {key_name((table,))} isn't a table the engine reads"
                             + (f"; did you mean {key_name((close[0],))}?" if close else "."))


def check_types(config: dict, origins: dict, schema: dict = LAYER_KEYS, path: tuple = ()) -> None:
    """The keys layers carry have their types wherever they're set: in a town's own file too."""
    for key, value in config.items():
        here = path + (key,)
        spec = schema.get(key, schema.get(ANY))
        if spec is None:
            continue
        if isinstance(spec, dict):
            if isinstance(value, dict):
                check_types(value, origins, spec, here)
        elif spec == TERM and term_problem(value):
            raise SystemExit(f"{origins.get(here, origins.get(here + ('en',), 'config'))}: {key_name(here)} should be "
                             f"{term_problem(value)}.")
        elif not fits(value, spec):
            raise SystemExit(f"{origins.get(here, 'config')}: {key_name(here)} should be {type_name(spec)}, not {value!r}.")


def mark(value, origin: str, origins: dict, path: tuple) -> None:
    """Record the file each value came from: every key of a table, and an array as one value."""
    if isinstance(value, dict):
        for key, v in value.items():
            mark(v, origin, origins, path + (key,))
    else:
        origins[path] = origin


def fill(config: dict, layer: dict, origin: str, origins: dict, path: tuple = ()) -> None:
    """Add what the layer has and the config doesn't yet, table by table. A value already set stays."""
    for key, value in layer.items():
        here = path + (key,)
        if key not in config:
            config[key] = copy.deepcopy(value)
            mark(value, origin, origins, here)
        elif isinstance(config[key], dict) and isinstance(value, dict):
            fill(config[key], value, origin, origins, here)


def define_glossary(config: dict, where: str, origins: dict) -> None:
    """Each [[glossary]] entry without a definition takes its term's from [terms]: the English definition,
    and, for a term given as a table of languages, the table too (languages), for its other languages."""
    terms = config.get("terms", {})
    for i, entry in enumerate(config.get("glossary", [])):
        if "definition" in entry:
            continue
        term = entry.get("term")
        if term not in terms:
            raise SystemExit(f"{where}: the [[glossary]] entry {term!r} has no definition, and no [terms] in the town's "
                             "file or its layers has one for it.")
        value = terms[term]
        if isinstance(value, dict):
            entry["definition"] = value["en"]["definition"]
            entry["languages"] = copy.deepcopy(value)
            origins[("glossary", i, "definition")] = origins.get(("terms", term, "en", "definition"), where)
        else:
            entry["definition"] = value
            origins[("glossary", i, "definition")] = origins.get(("terms", term), where)


def read_config(path: Path, network: Path | None = None, origins: dict | None = None) -> dict:
    """A town's config file merged over its layers (network: the network repository, else found from the
    file's folder), checked against the schema, with each glossary definition filled in. origins, if given,
    gets the file each value came from, by its path of keys. Nothing but the standard library: the
    network's plan job reads configs without the engine's packages."""
    origins = {} if origins is None else origins
    where = shown(path)
    own = read_toml(path)
    check_town(own, where)
    config = copy.deepcopy(own)
    mark(config, where, origins, ())
    network = network or network_dir(path.parent.parent)
    for layer_path in layer_files(network, own.get("town", {}).get("state_abbr")):
        layer = read_toml(layer_path)
        check_layer(layer, shown(layer_path))
        fill(config, {k: v for k, v in layer.items() if k in own or k in EVERY_TOWN}, shown(layer_path), origins)
    check_types(config, origins)
    define_glossary(config, where, origins)
    config["slug"] = path.stem
    return config


def load_config(town: str) -> dict:
    path = CONFIG_DIR / f"{town}.toml"
    if not town or not path.exists():
        raise SystemExit(f"No town config at {path}. Run from the town's repository, "
                         "or set PUBLICK_TOWN_DIR, and pass --town or set TOWN.")
    config = read_config(path, NETWORK_DIR)
    # What goes in the state-specific tables ([finance], [schools]) depends on the town's state.
    from pipeline import diligent, finalsite, ical, linkpage, meetingdocs, opengov, schedule, socrata, states
    states.check(config)
    # A meeting source's table that can't work fails here, not partway through a run.
    finalsite.check(config)
    linkpage.check(config)
    diligent.check(config)
    opengov.check(config)
    meetingdocs.check(config)
    ical.check(config)
    schedule.check(config)
    # And a 311 source's.
    socrata.check(config)
    return config


# The site's colors, as CSS custom properties. The defaults match site.css; a
# town sets its own in [site.colors]. "network" is the network's name in the
# masthead and share image (Publick slate), the same for every town.
DEFAULT_COLORS = {"primary": "#1e3e80", "primary_dark": "#011536", "primary_soft": "#e8edf6",
                  "accent": "#581824", "network": "#2c4a63"}


def colors(config: dict) -> dict:
    """The town's colors: the defaults, with any set in [site.colors]."""
    town = config["site"].get("colors", {})
    unknown = set(town) - set(DEFAULT_COLORS)
    if unknown:
        raise SystemExit(f"Unknown [site.colors] {', '.join(sorted(unknown))}; use {', '.join(DEFAULT_COLORS)}.")
    for name, value in town.items():
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value)):
            raise SystemExit(f'[site.colors] {name} = "{value}" is not a color like "#12469a".')
    return {**DEFAULT_COLORS, **town}


def configured(config: dict, table: str) -> bool:
    """Whether the town has a source. A town without one leaves its table out of
    the config file, and the command that fetches it does nothing."""
    if table in config:
        return True
    print(f"::notice::No [{table}] in config/{config['slug']}.toml; skipping.")
    return False


# ---- python -m pipeline.config show -------------------------------------------------------

def toml_value(value) -> str:
    """A value as TOML writes it, on one line."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{toml_key(k)} = {toml_value(v)}" for k, v in value.items()) + " }"
    return json.dumps(str(value), ensure_ascii=False)


def toml_key(key) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", str(key)) else json.dumps(key, ensure_ascii=False)


def is_tables(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(v, dict) for v in value)


def show_lines(table: dict, origins: dict, path: tuple = ()) -> list[str]:
    """A table's keys, each with the file it came from, then its tables and arrays of tables. An entry
    of an array of tables came from the file that set the array, but a glossary definition from [terms]."""
    name = ".".join(map(toml_key, path))
    lines = ["", f"[{name}]"] if path else []
    for key, value in table.items():
        if not isinstance(value, dict) and not is_tables(value):
            lines.append(f"{toml_key(key)} = {toml_value(value)}  # {origins.get(path + (key,), '')}")
    for key, value in table.items():
        if isinstance(value, dict):
            lines += show_lines(value, origins, path + (key,))
        elif is_tables(value):
            here = path + (key,)
            for i, entry in enumerate(value):
                lines += ["", f"[[{'.'.join(map(toml_key, here))}]]"]
                lines += [f"{toml_key(k)} = {toml_value(v)}  # {origins.get(here + (i, k), origins.get(here, ''))}"
                          for k, v in entry.items()]
    return lines


def show(path: Path, network: Path | None) -> str:
    origins: dict = {}
    config = read_config(path, network, origins)
    config.pop("slug")
    origins = {k: "town" if o == shown(path) else o for k, o in origins.items()}
    state = config.get("town", {}).get("state_abbr")
    files = [shown(path)] + [shown(f) for f in layer_files(network or network_dir(path.parent.parent), state)]
    head = [f"# {config.get('site', {}).get('name', path.stem)}: {', then '.join(files)}, the first that sets a key winning.",
            "# Each value is followed by where it came from: town (the town's own file), or a layer's file."]
    return "\n".join(head + show_lines(config, origins)).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    s = sub.add_parser("show", help="the town's config merged over its layers, each value with the file it came from")
    s.add_argument("--town", default=DEFAULT_TOWN, help="config/<town>.toml to show")
    args = parser.parse_args()
    path = CONFIG_DIR / f"{args.town}.toml"
    if not args.town or not path.exists():
        raise SystemExit(f"No town config at {path}. Set PUBLICK_TOWN_DIR, and pass --town or set TOWN.")
    sys.stdout.write(show(path, NETWORK_DIR))
    return 0


if __name__ == "__main__":
    sys.exit(main())
