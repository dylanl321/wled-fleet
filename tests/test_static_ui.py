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


def test_sheet_height_does_not_reuse_the_sheet_color_variable():
    # --sheet is the sheet's color. Setting it to a height made the background invalid.
    assert "--sheet:" not in MAIN
    assert "height: var(--sheet-height" in CSS


RIBBON = (ROOT / "app" / "static" / "lumen" / "ribbon.js").read_text(encoding="utf-8")
EFFECTS = (ROOT / "app" / "static" / "lumen" / "effects.js").read_text(encoding="utf-8")


def test_ribbons_draw_light_not_wood():
    # LED levels are encoded for the screen, so a dimmed amber strip is not brown.
    assert "map(toScreen)" in RIBBON
    # No stretched random texture (it read as wood grain) and no white lacquer highlight.
    assert "noiseCanvas" not in RIBBON
    assert "rgba(255,255,255,0.16)" not in RIBBON
    # Candle and fire flicker in smooth patches, not per-LED stripes toward dark red.
    assert "index * 1.7" not in EFFECTS
    assert "function smooth(" in EFFECTS


def test_looks_are_drawn_as_led_grids():
    assert "export function drawMatrix(" in RIBBON
    assert "drawMatrix(canvas" in MAIN
    # Colorloop is one color at a time across the strip, not a rainbow.
    assert 'name.includes("colorloop")' in EFFECTS


def test_copy_paste_and_show_outputs_are_wired():
    assert "data-copy" in MAIN and "data-paste" in MAIN
    assert "function pasteLook()" in MAIN and "commitLook(" in MAIN
    assert '"/api/lumen/outputs/show"' in MAIN and '"/api/lumen/outputs/hide"' in MAIN
    # Tapping Home outside a ribbon puts the sheet away.
    assert "return closeSheet();" in MAIN
