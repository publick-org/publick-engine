"""A town's config merged over the network's shared settings (pipeline/config.py): the network's layer,
then its state's, then the town's own file; what a layer may set; and the commands that read configs."""

import copy
import tomllib
from pathlib import Path

import pytest

from pipeline import config as config_mod
from pipeline import network
from pipeline.config import read_config, show

FIXTURE_TOWN = Path(__file__).parent / "fixtures" / "town" / "config" / "gloucester.toml"

NETWORK = """
[site]
network = "Publick"
timezone = "America/New_York"
languages = ["en", "es"]

[summaries]
model = "claude-sonnet-5"
input_price = 2.0
output_price = 10.0
max_per_run = 40

[seeclickfix]
api_base = "https://seeclickfix.com/api/v2"
request_delay = 3.2

[seeclickfix.repeats]
radius_m = 50

[digest]
signup = true
provider = "Buttondown"
privacy_url = "https://buttondown.com/legal/privacy"

[terms]
ADA = "Americans with Disabilities Act."
"executive session" = "A closed part of a meeting."

[strings.es]
"Americans with Disabilities Act." = "Ley de Estadounidenses con Discapacidades."
"""

STATE = """
[town]
state = "Massachusetts"

[housing]
bps_state = "25"
state_geo = "04000US25"

[terms]
"executive session" = "A part of a public meeting closed to the public, as Massachusetts law allows."
"M.G.L." = "Massachusetts General Laws."
"""

TOWN = """
[site]
name = "Salem Publick"
languages = ["en"]

[town]
name = "Salem"
state_abbr = "MA"

[[sections]]
slug = "about"
title = "About"

[summaries]
since = "2026-07-01"
max_per_run = 10

[housing]
bps_place = "59105"
state_geo = "04000US25"

[digest]

[[glossary]]
term = "M.G.L."

[[glossary]]
term = "executive session"
bodies = ["City Council"]

[[glossary]]
term = "ADA"
definition = "The town's own wording."
"""


def make_network(tmp_path, town=TOWN, network_layer=NETWORK, state_layer=STATE, folder="salem-ma"):
    root = tmp_path / "network"
    (root / "towns" / folder / "config").mkdir(parents=True)
    path = root / "towns" / folder / "config" / f"{folder.split('-')[0]}.toml"
    path.write_text(town)
    (root / "defaults" / "states").mkdir(parents=True)
    if network_layer is not None:
        (root / "defaults" / "network.toml").write_text(network_layer)
    if state_layer is not None:
        (root / "defaults" / "states" / "ma.toml").write_text(state_layer)
    return root, path


def test_a_town_without_layers_is_read_as_before():
    config = read_config(FIXTURE_TOWN)
    assert config == {**tomllib.loads(FIXTURE_TOWN.read_text()), "slug": "gloucester"}


def test_the_town_wins_over_its_state_and_its_state_over_the_network(tmp_path):
    root, path = make_network(tmp_path)
    config = read_config(path, root)
    # Tables merge key by key: the town's own keys, then its state's, then the network's.
    assert config["site"] == {"name": "Salem Publick", "languages": ["en"], "network": "Publick",
                              "timezone": "America/New_York"}
    assert config["town"] == {"name": "Salem", "state_abbr": "MA", "state": "Massachusetts"}
    assert config["summaries"] == {"since": "2026-07-01", "max_per_run": 10, "model": "claude-sonnet-5",
                                   "input_price": 2.0, "output_price": 10.0}
    assert config["housing"] == {"bps_place": "59105", "state_geo": "04000US25", "bps_state": "25"}
    # An empty table takes the network's settings; a table the town doesn't have is never added.
    assert config["digest"] == {"signup": True, "provider": "Buttondown", "privacy_url": "https://buttondown.com/legal/privacy"}
    assert "seeclickfix" not in config
    # [terms] and [strings] go to every town, the state's term winning over the network's.
    assert config["terms"]["executive session"].startswith("A part of a public meeting")
    assert config["strings"]["es"]["Americans with Disabilities Act."].startswith("Ley")
    assert config["slug"] == "salem"


def test_a_glossary_entry_with_only_its_term_takes_the_layers_definition(tmp_path):
    root, path = make_network(tmp_path)
    glossary = read_config(path, root)["glossary"]
    assert glossary == [
        {"term": "M.G.L.", "definition": "Massachusetts General Laws."},
        {"term": "executive session", "bodies": ["City Council"],
         "definition": "A part of a public meeting closed to the public, as Massachusetts law allows."},
        {"term": "ADA", "definition": "The town's own wording."},
    ]


def test_a_layer_fills_in_nested_tables_of_a_table_the_town_has(tmp_path):
    town = TOWN + '\n[seeclickfix]\norganization_id = 1\nrequest_delay = 5.0\n'
    root, path = make_network(tmp_path, town=town)
    assert read_config(path, root)["seeclickfix"] == {"organization_id": 1, "request_delay": 5.0,
                                                      "api_base": "https://seeclickfix.com/api/v2",
                                                      "repeats": {"radius_m": 50}}


def test_each_layer_is_optional(tmp_path):
    root, path = make_network(tmp_path, network_layer=None)
    config = read_config(path, root)
    assert config["town"]["state"] == "Massachusetts" and "model" not in config["summaries"]
    root, path = make_network(tmp_path / "b", state_layer=None, town=TOWN.replace('term = "M.G.L."', 'term = "ADA"'))
    config = read_config(path, root)
    assert "state" not in config["town"] and config["summaries"]["model"] == "claude-sonnet-5"


def test_the_network_is_found_from_the_towns_folder_or_by_name(tmp_path, monkeypatch):
    root, path = make_network(tmp_path)
    monkeypatch.delenv(config_mod.NETWORK_ENV, raising=False)
    assert config_mod.network_dir(path.parent.parent) == root.resolve()
    assert read_config(path)["town"]["state"] == "Massachusetts"
    assert config_mod.network_dir(tmp_path / "salem") is None
    monkeypatch.setenv(config_mod.NETWORK_ENV, str(root))
    assert config_mod.network_dir(tmp_path / "salem") == root.resolve()


@pytest.mark.parametrize("layer, message", [
    ("[summaries]\nmodle = 'x'\n", r"defaults/network.toml: \[summaries\] modle isn't a setting a layer can have; did you mean model\?"),
    ("[summaries]\ninput_price = 'two'\n", r"\[summaries\] input_price should be a number, not 'two'"),
    ("[summaries]\nmax_per_run = 4.5\n", r"\[summaries\] max_per_run should be a whole number"),
    ("[site]\nlanguages = 'en'\n", r"\[site\] languages should be a list of strings"),
    ("[seeclickfix.repeats]\nradius = 5\n", r"\[seeclickfix.repeats\] radius isn't a setting.*did you mean radius_m"),
    ("[[sections]]\nslug = 'about'\n", r"\[sections\] isn't a setting a layer can have"),
    ("[town]\nstate_abbr = 'MA'\n", r"\[town\] state_abbr isn't a setting a layer can have"),
    ("[terms]\nADA = 1\n", r"\[terms\] ADA should be a string"),
    ("[summaries]\neffort = 'hihg'\n", r'\[summaries\] effort should be one of "low", "medium", "high", "xhigh", "max", not \'hihg\''),
    ("[summaries]\ntranslation_effort = 'none'\n", r"\[summaries\] translation_effort should be one of"),
    ("[summaries]\nlong_prompt_tokens = 1e5\n", r"\[summaries\] long_prompt_tokens should be a whole number"),
])
def test_a_layer_may_set_only_the_keys_it_carries(tmp_path, layer, message):
    root, path = make_network(tmp_path, network_layer=layer)
    with pytest.raises(SystemExit, match=message):
        read_config(path, root)


def test_the_network_sets_the_summaries_effort_and_long_prompt_prices(tmp_path):
    layer = NETWORK.replace('model = "claude-sonnet-5"\ninput_price = 2.0\noutput_price = 10.0\n', '''model = "claude-haiku-5-5"
effort = "high"
input_price = 0.1
output_price = 0.5
long_prompt_tokens = 100_000
long_input_price = 0.5
long_output_price = 2.5
translation_model = "claude-haiku-5-5"
translation_effort = "high"
translation_input_price = 0.1
translation_output_price = 0.5
translation_review_effort = "low"
''')
    root, path = make_network(tmp_path, network_layer=layer)
    summaries = read_config(path, root)["summaries"]
    assert summaries == {"since": "2026-07-01", "max_per_run": 10, "model": "claude-haiku-5-5", "effort": "high",
                         "input_price": 0.1, "output_price": 0.5, "long_prompt_tokens": 100_000,
                         "long_input_price": 0.5, "long_output_price": 2.5, "translation_model": "claude-haiku-5-5",
                         "translation_effort": "high", "translation_input_price": 0.1, "translation_output_price": 0.5,
                         "translation_review_effort": "low"}


def test_a_towns_own_file_is_checked_too(tmp_path):
    root, path = make_network(tmp_path, town=TOWN + "\n[sumaries]\nsince = '2026-01-01'\n")
    with pytest.raises(SystemExit, match=r"salem.toml: \[sumaries\] isn't a table the engine reads; did you mean \[summaries\]\?"):
        read_config(path, root)
    root, path = make_network(tmp_path / "b", town=TOWN.replace('since = "2026-07-01"', 'since = "2026-07-01"\nmodel = 5'))
    with pytest.raises(SystemExit, match=r"salem.toml: \[summaries\] model should be a string, not 5"):
        read_config(path, root)
    root, path = make_network(tmp_path / "c", town=TOWN + '\n[[glossary]]\nterm = "CPA"\n')
    with pytest.raises(SystemExit, match=r"the \[\[glossary\]\] entry 'CPA' has no definition"):
        read_config(path, root)


def test_show_names_the_file_each_value_came_from(tmp_path, monkeypatch):
    root, path = make_network(tmp_path)
    monkeypatch.chdir(root)
    text = show(path.relative_to(root), root)
    assert 'model = "claude-sonnet-5"  # defaults/network.toml' in text
    assert 'state = "Massachusetts"  # defaults/states/ma.toml' in text
    assert 'max_per_run = 10  # town' in text
    assert '[[glossary]]\nterm = "M.G.L."  # town\ndefinition = "Massachusetts General Laws."  # defaults/states/ma.toml' in text
    assert text.startswith("# Salem Publick: towns/salem-ma/config/salem.toml, then defaults/states/ma.toml, then "
                           "defaults/network.toml")


def test_network_reads_merged_configs_and_rebuilds_the_towns_a_layer_changes(tmp_path):
    root, _ = make_network(tmp_path)
    (root / "towns" / "nashua-nh" / "config").mkdir(parents=True)
    (root / "towns" / "nashua-nh" / "config" / "nashua.toml").write_text('[town]\nname = "Nashua"\nstate_abbr = "NH"\n')
    assert network.town_config(root, "salem-ma")["town"]["state"] == "Massachusetts"
    towns = network.town_dirs(root)
    states = {t: network.town_state(root, t) for t in towns}
    assert states == {"nashua-nh": "nh", "salem-ma": "ma"}
    assert network.changed_towns(["defaults/network.toml"], towns, states) == towns
    assert network.changed_towns(["defaults/states/ma.toml"], towns, states) == ["salem-ma"]
    assert network.changed_towns(["defaults/states/ma.toml"], towns) == towns
    assert network.changed_towns(["defaults/states/vt.toml"], towns, states) == []
    changed = tmp_path / "changed.txt"
    changed.write_text("defaults/states/ma.toml\n")
    plan = network.plan(root, changed=changed.read_text().split())
    assert [b["towns"] for b in plan["include"]] == ["salem-ma"]
    assert network.town_env(root, "salem-ma")[config_mod.NETWORK_ENV] == str(root)


def test_merging_leaves_the_layers_as_they_were(tmp_path):
    root, path = make_network(tmp_path)
    before = copy.deepcopy(tomllib.loads((root / "defaults" / "network.toml").read_text()))
    first = read_config(path, root)
    first["summaries"]["model"] = "changed"
    assert read_config(path, root)["summaries"]["model"] == "claude-sonnet-5"
    assert tomllib.loads((root / "defaults" / "network.toml").read_text()) == before
