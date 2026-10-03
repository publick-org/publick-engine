"""Every prompt's version moves when the prompt does.

A saved summary, transcription, translation, or drafted text is made again only when its
prompt's version changes, so a prompt changed without its version leaves everything made
with the old one on the sites, as if it were current. Each prompt's words and schema are
pinned here by a hash beside its version: change a prompt, and this fails until either its
version goes up, or, where what the old prompt made should stay on purpose (an agenda's
start_time and location were added without a bump, so the agendas weren't all paid for
again; see summarize.py), the new hash is pinned with a note saying why.
"""

import hashlib
import json

import pytest

from pipeline import summarize, translate


def fingerprint(*parts) -> str:
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def kind(name):
    k = summarize.KINDS[name]
    return k["version"], fingerprint(k["system"], k["prompt"], k["schema"])


PROMPTS = {
    "summary of an agenda (summarize.KINDS)": lambda: kind("agenda"),
    "summary of minutes (summarize.KINDS)": lambda: kind("minutes"),
    "transcription (summarize.TRANSCRIBE)": lambda: (
        summarize.TRANSCRIBE["version"],
        fingerprint(summarize.TRANSCRIBE["system"], summarize.TRANSCRIBE["prompt"], summarize.TRANSCRIBE["schema"])),
    "translation (translate.VERSION)": lambda: (
        translate.VERSION,
        fingerprint(translate.SYSTEM, translate.PROMPT, translate.READERS, translate.schema("agenda"),
                    translate.schema("minutes"))),
    "drafted text (translate.NAMES_VERSION)": lambda: (
        translate.NAMES_VERSION, fingerprint(translate.NAMES_SYSTEM, translate.NAMES_PROMPT)),
}

# (version, hash of the prompt), as of 2026-10-03.
PINNED = {
    "summary of an agenda (summarize.KINDS)": (4, "5c47543097a4a51c"),
    "summary of minutes (summarize.KINDS)": (2, "00d5fa9e89a43292"),
    "transcription (summarize.TRANSCRIBE)": (1, "4294fa5198b8819f"),
    "translation (translate.VERSION)": (3, "fdd9a3df7a4c8741"),
    "drafted text (translate.NAMES_VERSION)": (2, "1ac57feb64f0ccfd"),
}


@pytest.mark.parametrize("name", PROMPTS)
def test_a_prompt_changes_with_its_version(name):
    version, now = PROMPTS[name]()
    pinned_version, pinned = PINNED[name]
    assert now == pinned or version != pinned_version, (
        f"The {name} prompt changed, but its version is still {version}. Raise it, so what the old prompt "
        f"made is made again, and pin ({version + 1}, \"{now}\") here; or, if what it made should stay, "
        f"pin ({version}, \"{now}\") with a note saying why.")
    assert (version, now) == (pinned_version, pinned), f"Pin ({version}, \"{now}\") for the {name} prompt here."
