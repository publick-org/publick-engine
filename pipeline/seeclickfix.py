"""Parsers for SeeClickFix public data.

Two public endpoints are used:
- Open311 GeoReport v2 (/open311/v2/<org>/requests.json): lists the
  organization's requests with category, location, and open/closed status.
  Paged, 100 per page, newest first.
- APIv2 (/api/v2/issues): lists requests by request type (request_types),
  submission time (after, before), last update (updated_at_after,
  updated_at_before) and status, each with what Open311 gives and the exact
  acknowledged, closed, and reopened times and detailed status (Open,
  Acknowledged, Closed, Archived). Paged, 100 per page, newest first, and no
  further than page 25. /api/v2/issues/<id> is one request.

Descriptions, photos, and reporter details are not stored.
"""

from __future__ import annotations

import re


def short_address(address: str, town: str) -> str:
    """'29 Emerson Avenue Gloucester, Massachusetts, 01930' -> '29 Emerson Avenue',
    given the town's name ('Gloucester')."""
    short = re.split(rf",?\s+{re.escape(town)}\b", address or "", maxsplit=1, flags=re.I)[0].strip(" ,")
    return short or address


def street_address(address: str, town: str) -> str:
    """The address as SeeClickFix lists it, without the town, state, and ZIP code."""
    short = short_address(address, town)
    return "" if re.fullmatch(r"\d{5}", short) else short  # a ZIP code alone


# Categories whose requests point at a person or a household rather than at the street: an
# encampment, a complaint to the health department or the police, a neighbor's property or noise,
# or a request that says something about the home it came from (a smoke detector, a lost pet, a lead
# pipe inspection). Their addresses are shown only to the block, and their map points to about 100
# meters. A town adds its own category names in [seeclickfix] sensitive_categories.
SENSITIVE_CATEGORIES = re.compile(
    r"encampment|homeless|health department|problem property|noise|police|private property"
    r"|smoke detector|lost or missing pet|lead service", re.I)
# A house number, or a range of them ("62-66", "144–172"), perhaps with a half ("25 1/2", "25½"),
# at the start of an address.
HOUSE_NUMBER = re.compile(r"^#?(\d+)[A-Za-z]?(?:\s*[-–/&]\s*\d+[A-Za-z]?)*(?:\s*(?:1/2|½))?\s+(.+)$")
UNIT = re.compile(r"\s+(?:unit|apt|#)\s*\S+$", re.I)
# Decimal places for a sensitive request's map point: about 110 meters north to south.
COARSE_DIGITS = 3


# Where a category's name goes on into the city's instructions to whoever files one: "Noise Complaint
# - IF THIS IS AN EMERGENCY, PLEASE CALL 911...", "Drainage/Flooding Issue - NON-EMERGENCY SITUATIONS
# ONLY...", "Public Health Nuisance - Odors *IF YOU SMELL GAS...".
INSTRUCTIONS = re.compile(r"\s*(?:-+|\*+)\s*\**\s*(?:if\b|please\b|non-emergency\b|for (?:an )?emergenc)", re.I)


def category_label(category: str) -> str:
    """A category's name without the instructions some cities put in it, for a page to show:
    "Noise Complaint - IF THIS IS AN EMERGENCY, PLEASE CALL 911. ..." -> "Noise Complaint"."""
    cut = INSTRUCTIONS.search(category or "")
    return category[:cut.start()].rstrip(" -*:") if cut and cut.start() > 0 else category


def sensitive(category: str, extra: list[str] | tuple = ()) -> bool:
    """Whether a category's requests are shown only to the block (SENSITIVE_CATEGORIES)."""
    return bool(SENSITIVE_CATEGORIES.search(category or "")) or category in extra


def block_address(address: str, town: str) -> str:
    """The address to its hundred block, a range of house numbers that reads the same in every
    language: '67 Middle St' -> '1–99 Middle St', '262 Main Street Apt 4' -> '200–299 Main Street'.
    An intersection or a landmark has no house number and is kept as it is."""
    short = street_address(address, town)
    m = HOUSE_NUMBER.match(short)
    if not m:
        return short
    n = int(m.group(1))
    low = n // 100 * 100
    return f"{low or 1}–{low + 99} {UNIT.sub('', m.group(2))}"


def parse_open311(item: dict) -> dict:
    return {
        "id": str(item["service_request_id"]),
        "category": (item.get("service_name") or "Uncategorized").strip(),
        "service_code": item.get("service_code"),
        "status": (item.get("status") or "").lower(),  # "open" includes acknowledged
        "created_at": item.get("requested_datetime"),
        "updated_at": item.get("updated_datetime"),
        "lat": item.get("lat"),
        "lng": item.get("long"),
        "address": (item.get("address") or "").strip(),
    }


def parse_listed(issue: dict) -> dict:
    """An APIv2 request as parse_open311 reads Open311's: the two list the same request types,
    names, places and times. Acknowledged is open there, and archived closed."""
    request_type = issue.get("request_type") or {}
    return {
        "id": str(issue["id"]),
        "category": (request_type.get("title") or "Uncategorized").strip(),
        "service_code": request_type.get("id"),
        "status": "open" if (issue.get("status") or "").lower() in ("open", "acknowledged") else "closed",
        "created_at": issue.get("created_at"),
        "updated_at": issue.get("updated_at"),
        "lat": issue.get("lat"),
        "lng": issue.get("lng"),
        "address": (issue.get("address") or "").strip(),
    }


def parse_issue(issue: dict) -> dict:
    return {
        "status": (issue.get("status") or "").lower(),  # open, acknowledged, closed, archived
        "acknowledged_at": issue.get("acknowledged_at"),
        "closed_at": issue.get("closed_at"),
        "reopened_at": issue.get("reopened_at"),
        "updated_at": issue.get("updated_at"),
        "created_at": issue.get("created_at"),
    }
