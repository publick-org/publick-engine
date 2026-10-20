"""Every prompt's version moves when the prompt does.

A saved summary or transcription is made again only when its prompt's version changes, so a
prompt changed without its version leaves everything made with the old one on the sites, as if
it were current. A translation or drafted text keeps its prompt's version as a record of what
made it: raising translate.VERSION or NAMES_VERSION makes nothing again (a translation is made
again when it fails the checks, or when REMAKE_BELOW or NAMES_REMAKE_BELOW is raised on purpose).
Each prompt's words and schema are pinned here by a hash beside its version: change a prompt, and
this fails until either its version goes up, or, where what the old prompt made should stay on
purpose (an agenda's start_time and location were added without a bump, so the agendas weren't
all paid for again; see summarize.py), the new hash is pinned with a note saying why.
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
        fingerprint(translate.SYSTEM, translate.PROMPT, translate.CORRECT, translate.READERS, translate.GLOSSARY,
                    translate.schema("agenda"), translate.schema("minutes"))),
    "drafted text (translate.NAMES_VERSION)": lambda: (
        translate.NAMES_VERSION, fingerprint(translate.NAMES_SYSTEM, translate.NAMES_PROMPT, translate.GLOSSARY)),
}

# (version, hash of the prompt), as of 2026-10-16.
PINNED = {
    "summary of an agenda (summarize.KINDS)": (5, "22ed04a0cc5534fe"),
    "summary of minutes (summarize.KINDS)": (4, "1cbeee13a57af57b"),
    "transcription (summarize.TRANSCRIBE)": (1, "4294fa5198b8819f"),
    "translation (translate.VERSION)": (6, "94739fb55f4b1f5f"),
    # The town's glossary added to the drafting prompt (2026-10-16) without a bump: it only adds words, and
    # drafts made before it, each checked and reviewed, stay shown rather than all made again at once.
    "drafted text (translate.NAMES_VERSION)": (2, "3df0dba9c6397f65"),
}


@pytest.mark.parametrize("name", PROMPTS)
def test_a_prompt_changes_with_its_version(name):
    version, now = PROMPTS[name]()
    pinned_version, pinned = PINNED[name]
    assert now == pinned or version != pinned_version, (
        f"The {name} prompt changed, but its version is still {version}. Raise it, and pin ({version + 1}, "
        f"\"{now}\") here (a summary or transcription is then made again; a translation or draft only if it fails "
        f"its checks, or its REMAKE_BELOW is raised too); or, if what it made should stay as current, pin ({version}, "
        f"\"{now}\") with a note saying why.")
    assert (version, now) == (pinned_version, pinned), f"Pin ({version}, \"{now}\") for the {name} prompt here."
