"""Theme layer: palettes are complete and consistent, verdict copy maps
correctly, and the stylesheet/title-card helpers are well-formed. Pure —
no Streamlit runtime needed (we don't call active())."""

from app import theme


def test_palettes_share_the_same_tokens():
    # every CSS variable defined in one palette must exist in the other, so a
    # theme flip never leaves an undefined var() in the rendered HTML.
    assert set(theme.PALETTES["docket"]) == set(theme.PALETTES["original"])


def test_palette_values_are_colors():
    for pal in theme.PALETTES.values():
        for k, v in pal.items():
            if k.endswith("-rgb"):
                assert v.count(",") == 2  # "r,g,b"
            else:
                assert v.startswith("#")


def test_css_injects_vars_and_fonts_for_each_theme():
    for name in theme.THEMES:
        css = theme.css(name)
        assert css.startswith("<style>") and css.rstrip().endswith("</style>")
        assert "--acc:" in css and "--card:" in css and "--disp:" in css
        # the docket skin carries the courthouse + typewriter faces
        if name == "docket":
            assert "Cinzel" in css and "Courier Prime" in css


def test_verdict_labels_map_only_on_docket(monkeypatch):
    monkeypatch.setattr(theme, "is_docket", lambda: True)
    assert theme.verdict("PLAY") == "INDICTED"
    assert theme.verdict("PASS") == "DISMISSED"
    assert theme.verdict("NOTE") == "EXHIBIT"
    monkeypatch.setattr(theme, "is_docket", lambda: False)
    assert theme.verdict("PLAY") == "PLAY"  # untouched off-theme


def test_section_title_lookup(monkeypatch):
    monkeypatch.setattr(theme, "is_docket", lambda: True)
    assert theme.t("PLAYS", "Plays") == "The Daily Docket"
    assert theme.t("UNKNOWN", "Fallback") == "Fallback"
    monkeypatch.setattr(theme, "is_docket", lambda: False)
    assert theme.t("PLAYS", "Plays") == "Plays"


def test_title_card_and_stamp_html():
    tc = theme.title_card("Eyebrow", "TITLE", "sub line")
    assert "osp-tcard" in tc and "TITLE" in tc and "sub line" in tc
    st = theme.stamp("INDICTED", "indicted")
    assert "osp-stamp indicted" in st and "INDICTED" in st
