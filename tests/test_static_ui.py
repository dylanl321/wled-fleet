from pathlib import Path


ROOT = Path(__file__).parents[1]
MAIN = (ROOT / "app" / "static" / "lumen" / "main.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")
HTML = (ROOT / "app" / "static" / "index.html").read_text(encoding="utf-8")


def test_lumen_has_no_tab_bar_and_the_ribbons_are_the_interface():
    assert "tabbar" not in CSS
    assert "data-screen" in MAIN
    assert "data-look-tray" in MAIN
    assert "data-paint" in MAIN
    assert "data-ruler" in MAIN
    assert "data-ribbon" in MAIN
    assert "navigator.vibrate" in MAIN
    assert "#0b0b0d" in CSS
    assert "lumen/main.js" in HTML
    assert 'const CACHE = "lumen-1";' in SW_JS
    assert "self.skipWaiting()" in SW_JS
    assert "self.clients.claim()" in SW_JS
