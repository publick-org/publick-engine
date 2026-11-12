"""The search's answers (pipeline/search_answers.py): what each is made from, and how names and searches are read."""

import json

from pipeline import search_answers


def test_words_read_a_question_and_a_street_type_the_same_way():
    assert search_answers.words("Who is the Mayor?") == ["mayor"]
    assert search_answers.words("pothole on Main St.") == ["pothole", "main", "st"]
    assert search_answers.words("Sargent Street") == search_answers.words("sargent st") == ["sargent", "st"]
    assert search_answers.words("Farmer’s Market") == ["farmers", "market"]
    # A word that can be part of a board's name is kept.
    assert search_answers.words("Town Meeting") == ["town", "meeting"]
    assert search_answers.words("¿Cuándo se reúne el Concejo Municipal?", "es") == ["reune", "concejo", "municipal"]
    # A contraction is a question's word too; "IT" isn't one.
    assert search_answers.words("Who's the mayor") == ["mayor"]
    assert search_answers.words("IT department") == ["it", "department"]
    # Directions as addresses abbreviate them, and letters that keep no letter without their accent.
    assert search_answers.words("East Main Street") == search_answers.words("E Main St") == ["e", "main", "st"]
    assert search_answers.words("Søren Łukasz") == ["soren", "lukasz"]


def test_street_answers():
    index = {"streets": {
        "SARGENT ST": {"name": "Sargent Street", "meetings_total": 2, "requests_total": 12, "permits_total": 0},
        "EMPTY RD": {"name": "Empty Road", "meetings_total": 0, "requests_total": 0, "permits_total": 0},
    }}
    [sargent] = search_answers.street_answers(index, "/es")
    assert sargent == {"k": "street", "n": "Sargent Street", "u": "/es/streets/?q=Sargent%20Street",
                       "c": {"meetings": 2, "requests": 12}}


def test_board_answers():
    boards = [{"name": "Junta de Salud", "name_en": "Board of Health", "url": "/meetings/boards/board-of-health/",
               "next": {"date": "2026-10-08", "status": "scheduled"},
               "meetings": [{"date": "2026-10-08", "status": "scheduled"}, {"date": "2026-09-10", "status": "scheduled"},
                            {"date": "2026-08-13", "status": "scheduled"}]}]
    [health] = search_answers.board_answers(boards, "2026-10-02", "/es")
    assert health == {"k": "board", "n": "Junta de Salud", "a": ["Board of Health"],
                      "u": "/es/meetings/boards/board-of-health/", "next": "2026-10-08", "last": "2026-09-10"}
    # A meeting cancelled or postponed isn't one the board meets, or met.
    boards[0]["next"]["status"] = boards[0]["meetings"][0]["status"] = "cancelled"
    boards[0]["meetings"][1]["status"] = "postponed"
    [health] = search_answers.board_answers(boards, "2026-10-02", "/es")
    assert "next" not in health and health["last"] == "2026-08-13"


def test_official_answers():
    officials = {"bodies": [
        {"name": "Mayor", "show_seat": False, "members": [{"name": "Pat Doe", "seat": "Citywide", "anchor": "mayor-pat-doe"}]},
        {"name": "City Council", "show_seat": True, "members": [
            {"name": "Lee Roe", "seat": "Ward 2", "role": "Council President", "anchor": "city-council-lee-roe"},
            {"name": "Kim Poe", "seat": "At-large", "anchor": "city-council-kim-poe"}]},
        {"name": "School Committee", "show_seat": True, "members": [
            {"name": "Pat Doe", "seat": "Mayor", "role": "Chair", "anchor": "school-committee-pat-doe"},
            {"name": "Ash Moe", "seat": "At-large", "anchor": "school-committee-ash-moe"}]},
    ]}
    answers = {a["n"]: a for a in search_answers.official_answers(officials, "")}
    # The mayor, found as "mayor" (a body of one), and on the School Committee too, under one answer, without
    # saying "Mayor" again for their seat there.
    assert answers["Pat Doe"] == {"k": "official", "n": "Pat Doe", "u": "/officials/#mayor-pat-doe",
                                  "d": "Mayor; School Committee, Chair",
                                  "a": ["Mayor", "School Committee Mayor", "School Committee Chair"]}
    assert answers["Ash Moe"]["d"] == "School Committee, At-large"
    # A role is named with its body, without saying "Council" twice.
    assert answers["Lee Roe"]["d"] == "City Council, Ward 2, Council President"
    # Found by their seat, on a body whose seats differ, and their role.
    assert answers["Lee Roe"]["a"] == ["City Council Ward 2", "City Council President"]
    assert answers["Kim Poe"]["a"] == ["City Council At-large"]


def test_answers_file(site_dir):
    """The site writes its answers for the search page, which loads them from the address it gives."""
    data = json.loads((site_dir / "search" / "answers.json").read_text())
    assert "who" in data["stop"] and data["suffixes"]["street"] == "st" and data["directions"]["east"] == "e"
    kinds = {a["k"] for a in data["answers"]}
    assert kinds == {"street", "board", "official"}
    assert all(a["u"].startswith("/") for a in data["answers"])
    page = (site_dir / "search" / "index.html").read_text()
    assert 'data-answers="/search/answers.json?v=' in page
