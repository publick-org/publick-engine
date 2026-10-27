"""Read 311 requests from a city's Socrata open data portal ([socrata_311]).

A Socrata dataset is queried with SoQL through its resource address,
https://<domain>/resource/<dataset>.json: $select, $where, $order and $limit.
Each row also has two system fields: :id, and :updated_at, when the portal
last changed the row. A city that loads its 311 system's requests into the
portal every day rewrites each request whose status changed, so :updated_at
says which requests changed since a time.

Somerville's dataset (4pyi-uqq6 on data.somervillema.gov) has, for each
request, its class (Service, Information or Feedback), category, type, how it
came in, when it was created, its most recent status (Open, In Progress, On
Hold or Closed) and that status's date, its ward and its census block. It has
no address, map point or acknowledged time. Its column names are the defaults
of [socrata_311.fields]; another city's dataset names its own.

Only what the 311 pages use is kept: no census block, and no description or
reporter, which these datasets don't publish anyway.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

# Each field of a request, and the dataset column it's read from unless [socrata_311.fields] names another.
# A field set to "" isn't read: a dataset without wards sets ward = "".
FIELDS = {
    "id": "id",
    "created": "date_created",
    "status": "most_recent_status",
    "status_date": "most_recent_status_date",
    "category": "category",
    "type": "type",
    "ward": "ward",
    "class": "classification",
    "origin": "origin_of_request",
}
# Fields every dataset must have.
REQUIRED = ("id", "created", "status", "status_date", "category")
# The statuses that close a request, unless [socrata_311] closed_statuses names others.
CLOSED_STATUSES = ("Closed",)
# A column name SoQL takes bare.
COLUMN = re.compile(r"[a-z_][a-z0-9_]*")


def fields(settings: dict) -> dict:
    """The dataset's column for each field, with the town's names over the defaults; a field
    the dataset doesn't have is left out."""
    return {k: v for k, v in {**FIELDS, **settings.get("fields", {})}.items() if v}


def quote(value: str) -> str:
    """A SoQL string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def when(value: str | None, tz: ZoneInfo) -> str | None:
    """A dataset's local time, as text ("2026-10-26 22:58:00") or a floating timestamp
    ("2026-10-26T22:58:00.000"), as an ISO time with the town's offset."""
    if not value:
        return None
    local = datetime.fromisoformat(value.replace(" ", "T")[:19])
    return local.replace(tzinfo=tz).isoformat(timespec="seconds")


def updated_at(row: dict) -> str:
    """A row's :updated_at, as the portal gives it (UTC, "2026-10-27T06:04:12.247Z")."""
    return row[":updated_at"]


def zulu(dt: datetime) -> str:
    """A time as :updated_at is compared: UTC, to the millisecond."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_row(row: dict, columns: dict, closed: set[str], tz: ZoneInfo) -> dict:
    """A dataset row as a 311 request. A request is closed when its most recent status is one of
    closed, and then the status's date is when it was closed."""
    status = (row.get(columns["status"]) or "").strip()
    record = {
        "id": str(row[columns["id"]]),
        "category": (row.get(columns["category"]) or "").strip() or "Uncategorized",
        "status": "closed" if status in closed else "open",
        "status_detail": status,
        "created_at": when(row.get(columns["created"]), tz),
        # The date of the most recent status: when a closed request was closed.
        "updated_at": when(row.get(columns["status_date"]), tz),
    }
    if "type" in columns:
        record["type"] = (row.get(columns["type"]) or "").strip() or None
    if "origin" in columns:
        record["origin"] = (row.get(columns["origin"]) or "").strip() or None
    if "ward" in columns:
        # As the city records it; a request with none isn't placed in a ward.
        ward = str(row.get(columns["ward"]) or "").strip()
        record["ward"] = ward or None
    return record


def check(config: dict) -> None:
    """A town's [socrata_311] table, checked when its config is loaded."""
    settings = config.get("socrata_311")
    if settings is None:
        return
    where = f"[socrata_311] in config/{config['slug']}.toml"
    if "seeclickfix" in config:
        raise SystemExit(f"{where}: the town also has [seeclickfix]. Its 311 requests come from one source; keep one table.")
    missing = [key for key in ("domain", "dataset", "since", "publisher") if not settings.get(key)]
    if missing:
        raise SystemExit(f"{where} needs {', '.join(missing)}: the portal's domain, the dataset's id, the first "
                         "month to collect, and who publishes it.")
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", str(settings["domain"])):
        raise SystemExit(f'{where}: domain must be the portal\'s host name, like "data.somervillema.gov".')
    if not re.fullmatch(r"[a-z0-9]{4}-[a-z0-9]{4}", str(settings["dataset"])):
        raise SystemExit(f'{where}: dataset must be the dataset\'s id, like "4pyi-uqq6".')
    if not re.fullmatch(r"\d{4}-\d{2}", str(settings["since"])):
        raise SystemExit(f'{where}: since must be a month, like "2024-07".')
    unknown = set(settings.get("fields", {})) - set(FIELDS)
    if unknown:
        raise SystemExit(f"{where}: fields names {', '.join(sorted(unknown))}; it takes {', '.join(FIELDS)}.")
    columns = fields(settings)
    if missing := [f for f in REQUIRED if f not in columns]:
        raise SystemExit(f"{where}: fields can't leave out {', '.join(missing)}.")
    if bad := [c for c in columns.values() if not COLUMN.fullmatch(c)]:
        raise SystemExit(f"{where}: fields names columns SoQL can't read: {', '.join(bad)}. Use the dataset's "
                         "API field names, like date_created.")
    if settings.get("classes") is not None:
        if "class" not in columns:
            raise SystemExit(f"{where}: classes needs the class field, the column that holds them.")
        if not isinstance(settings["classes"], list) or not all(isinstance(c, str) and c for c in settings["classes"]):
            raise SystemExit(f'{where}: classes must be a list of the classes to keep, like ["Service"].')
    if "ward" in columns:
        if config.get("town", {}).get("areas", "wards") != "wards":
            raise SystemExit(f"{where}: the dataset's ward column is read as wards, and [town] areas says "
                             f"{config['town']['areas']}. Set fields.ward = \"\" to leave areas out.")
        if not (settings.get("wards_file") or config.get("officials", {}).get("wards_file")):
            raise SystemExit(f"{where} needs wards_file, or [officials] wards_file: the ward file in data/static/ whose "
                             "features give each ward's population_2020, for requests per 1,000 residents. A dataset "
                             'without wards sets fields.ward = "".')
    if bool(settings.get("license")) != bool(settings.get("license_url")):
        raise SystemExit(f"{where}: license and license_url go together: the license's name and its address.")
    statuses = settings.get("closed_statuses", list(CLOSED_STATUSES))
    if not isinstance(statuses, list) or not statuses or not all(isinstance(s, str) and s for s in statuses):
        raise SystemExit(f'{where}: closed_statuses must be a list of the statuses that close a request, like ["Closed"].')
