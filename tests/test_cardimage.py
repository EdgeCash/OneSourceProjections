"""Tests for the card->PNG image module. The browser-dependent render is
guarded so it runs where Chromium is present and is skipped otherwise."""

from project547 import cardimage


def test_wrap_html_has_theme_and_card():
    html = cardimage.wrap_html("<div id='card'>hi</div>", width=900)
    assert "--text:" in html and "--good:" in html      # brand vars defined
    assert "id='card'" in html and "width:900px" in html


def test_render_png_none_without_browser(monkeypatch):
    monkeypatch.setattr(cardimage, "chrome_path", lambda: None)
    assert cardimage.render_png("<div>x</div>") is None
    assert cardimage.available() is False


def test_render_png_when_browser_present():
    if not cardimage.available():
        import pytest
        pytest.skip("no Chromium in this environment")
    png = cardimage.render_png("<div style='padding:20px;font-size:30px;'>"
                               "Project 54.7</div>", width=400)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG header
