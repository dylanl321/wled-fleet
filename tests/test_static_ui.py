from pathlib import Path
import re


ROOT = Path(__file__).parents[1]
APP_JS = (ROOT / "app" / "static" / "app.js").read_text(encoding="utf-8")
CSS = (ROOT / "app" / "static" / "styles.css").read_text(encoding="utf-8")
SW_JS = (ROOT / "app" / "static" / "sw.js").read_text(encoding="utf-8")


def test_groups_have_visible_selection_and_one_editor():
    assert "+ Group" in APP_JS
    assert 'data-action="edit-group"' in APP_JS
    assert 'data-action="group-member"' in APP_JS
    assert 'data-action="save-group"' in APP_JS
    assert 'aria-pressed="' in APP_JS
    assert "Delete group?" in APP_JS
    assert "function groupSheet()" in APP_JS


def test_controller_rename_hit_target_is_intrinsic_width():
    name_rule = re.search(r"^\.namebtn \{([^}]*)\}", CSS, re.MULTILINE).group(1)
    strip_rule = re.search(r"^\.strip-head \.namebtn \{([^}]*)\}", CSS, re.MULTILINE).group(1)
    assert "width: auto" in name_rule
    assert "width: 100%" not in name_rule
    assert "flex: 1" not in strip_rule


def test_navigation_discards_stale_async_loads_and_stale_route_state():
    assert "let loadGeneration = 0;" in APP_JS
    assert "let controlGeneration = 0;" in APP_JS
    assert "generation !== loadGeneration" in APP_JS
    assert "requestId !== controlGeneration" in APP_JS
    assert "controlRequestId !== controlGeneration" in APP_JS
    assert "load(false, { paintEarly: false })" in APP_JS
    assert "function clearRouteState(route)" in APP_JS
    assert "const backup = await api" in APP_JS
    assert "const events = await api" in APP_JS
    assert "const settings = await api" in APP_JS


def test_sheets_are_modal_and_restore_exact_focus():
    assert ".inert = Boolean(state.sheet)" in APP_JS
    assert "state.sheetReturn" in APP_JS
    assert "querySelectorAll" in APP_JS
    assert "returnSheet" in APP_JS
    assert 'querySelector("[autofocus], input, button:not(.backdrop)")' in APP_JS


def test_scene_save_has_a_real_and_current_look_payload():
    assert "function currentLook()" in APP_JS
    assert "function applyLookToComposer(look)" in APP_JS
    assert "function lookFromWledState(raw)" in APP_JS
    assert "const applied = (results.results || []).some((row) => row.ok);" in APP_JS
    assert "if (scene && applied) applyLookToComposer(scene.look);" in APP_JS
    assert 'body: JSON.stringify({ name, look: currentLook() })' in APP_JS


def test_service_worker_replaces_previous_static_assets():
    assert 'const CACHE = "wled-fleet-4";' in SW_JS
    assert "self.skipWaiting()" in SW_JS
    assert "self.clients.claim()" in SW_JS


def test_control_tools_have_clear_information_architecture():
    for label in (
        "Customize look",
        "Controller presets",
        "Saved looks",
        "Tools",
        "Identify LED sections",
        "Back up all controllers",
    ):
        assert label in APP_JS
