"""Notes marked data-about are gathered under "About this page" at the end of a page's content (build_site.gather_notes)."""
from pipeline import build_site
from pipeline.seeclickfix import category_label

PAGE = ('<main><div class="wrap"><h1>Budget</h1><p class="source-note" data-about>Checked October 1.</p>'
        '<table></table><p class="small">Not adjusted for inflation.</p>'
        '<p class="small" data-about>Source: <a href="https://example.org">DLS</a>.</p>\n'
        '<!--about-page-->\n</div></main>')


def test_notes_move_in_order_to_the_end_of_the_content():
    html = build_site.gather_notes(PAGE, "About this page")
    assert "data-about" not in html and "<!--about-page-->" not in html
    about = html.index('<h2 id="about-page">About this page</h2>')
    assert html.index("Not adjusted for inflation") < about < html.index("Checked October 1") < html.index("Source:")
    assert html.index("Source:") < html.index("</div></main>")


def test_a_page_without_notes_has_no_section_or_marker():
    html = build_site.gather_notes("<main><h1>Hi</h1>\n<!--about-page-->\n</main>", "About this page")
    assert html == "<main><h1>Hi</h1>\n</main>"


def test_category_label_drops_the_citys_instructions():
    assert category_label("Noise Complaint - IF THIS IS AN EMERGENCY, PLEASE CALL 911. PLEASE NOTE: ...") == "Noise Complaint"
    assert category_label("Domestic Water & Sewage - **If this request if after 4pm ...") == "Domestic Water & Sewage"
    assert category_label("Drainage/Flooding Issue - NON-EMERGENCY SITUATIONS ONLY - if this is ...") == "Drainage/Flooding Issue"
    assert category_label("Public Health Nuisance - Odors *IF YOU SMELL GAS - PLEASE CALL 911*") == "Public Health Nuisance - Odors"
    assert category_label("Noise/ Business and Construction activity- IF THIS IS AN EMERGENCY") == "Noise/ Business and Construction activity"
    for name in ("Pothole", "Trees: Fallen / Broken Limbs", "Dog Bite (Animal on Animal)", "Overflowing Trash - Park"):
        assert category_label(name) == name
