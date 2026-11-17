"""Full-list discovery, display-only visibility and terminal viewport regression tests."""
from contextlib import ExitStack
import curses
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import competition_menu as menu
import competition_menu_data as data
import competition_queue as cq
import competition_quick as quick

JOINTS = ("base_yaw", "shoulder", "elbow", "wrist", "gripper")
WIN = "winning_line_c1r1_c2r1_c3r1"


def point(value=2000):
    return dict(zip(JOINTS, [value, 2000, 2000, 2000, 1500]))


class Editor:
    def __init__(self, folder):
        self.folder = folder
        self.BOOKMARK_FILE = folder / "_pose_bookmarks.json"
        self.settings = SimpleNamespace(JOINT_NAMES=JOINTS)
        self.competition = cq
        self.wp = SimpleNamespace(find_sequences=self.find, load_sequence=self.load)
        self.connect = Mock(side_effect=AssertionError("Must not connect to arm"))
        self.refresh_attacks = Mock(return_value=0)

    def find(self):
        # Deliberately simulate an old finder that misses winning-line exports.
        return [(self.folder / "grab1.json", json.loads((self.folder / "grab1.json").read_text()))]

    def load(self, path):
        raw = json.loads(path.read_text())
        return raw, [dict(zip(JOINTS, p["input_values"])) for p in raw["points"]]

    def validate_competition_target(self, pose):
        if set(pose) != set(JOINTS) or not all(type(v) is int and 0 <= v <= 4095 for v in pose.values()):
            raise ValueError("Invalid servo values")


def write_recording(folder, name, *, description="Taught", value=2000):
    payload = {"type": "pose_sequence", "input_type": "raw", "name": name,
               "description": description,
               "points": [{"input_values": list(point(value).values())},
                          {"input_values": list(point(value+1).values())}]}
    path = folder / (name + ".json")
    path.write_text(json.dumps(payload))
    return path


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / "motions"
        self.folder.mkdir()
        write_recording(self.folder, "grab1")
        self.editor = Editor(self.folder)

    def test_supplemental_scan_finds_winning_lines_without_changing_files(self):
        write_recording(self.folder, WIN)
        write_recording(self.folder, "custom_extra")
        before = {p.name: p.read_bytes() for p in self.folder.iterdir()}
        snapshot = data.build_snapshot(self.editor)
        self.assertIn(WIN, snapshot.aliases)
        self.assertIn("custom_extra", snapshot.aliases)
        self.assertEqual(snapshot.aliases["grab1"], "1")
        self.assertEqual({p.name: p.read_bytes() for p in self.folder.iterdir()}, before)
        self.editor.connect.assert_not_called()

    def test_invalid_and_simulation_recordings_are_visible_but_not_executable(self):
        write_recording(self.folder, "bad", value=9000)
        write_recording(self.folder, "estimate", description="OFFLINE SIMULATION ONLY")
        snapshot = data.build_snapshot(self.editor)
        unavailable = {name: reason for name, reason in snapshot.unavailable.values()}
        self.assertIn("bad", unavailable)
        self.assertIn("Invalid servo", unavailable["bad"])
        self.assertIn("simulation-only", unavailable["estimate"])
        self.assertNotIn("estimate", snapshot.aliases)
        self.assertNotIn("bad", snapshot.aliases)
        self.assertEqual(len(snapshot.all_labels), 3)
        self.editor.connect.assert_not_called()

    def test_malformed_json_has_reason_but_metadata_is_not_a_motion(self):
        (self.folder / "broken.json").write_text("not json")
        (self.folder / "_competition_presets.json").write_text("not json")
        (self.folder / "_other_config.json").write_text('{"mode": "unused"}')
        snapshot = data.build_snapshot(self.editor)
        names = list(snapshot.all_labels.values())
        self.assertIn("broken", names)
        self.assertNotIn("_competition_presets", names)
        self.assertNotIn("_other_config", names)
        self.assertTrue(next(iter(snapshot.unavailable.values()))[1])

    def test_legacy_finder_failure_still_discovers_actual_recordings(self):
        write_recording(self.folder, WIN)
        self.editor.wp.find_sequences = Mock(side_effect=ValueError("finder failed"))
        snapshot = data.build_snapshot(self.editor)
        self.assertIn(WIN, snapshot.aliases)
        self.assertIn("grab1", snapshot.aliases)
        self.assertIn("finder failed", " ".join(snapshot.notes))

    def test_same_catalog_used_for_quick_playback_and_ui(self):
        write_recording(self.folder, WIN)
        catalog, labels, aliases = quick.catalog_for(self.editor, report=lambda _: None)
        plan, names = quick.resolve_queue([WIN], catalog, labels, aliases)
        self.assertEqual(names, (WIN,))
        self.assertEqual(len(plan.catalog[plan.pending[0]]), 2)
        self.editor.connect.assert_not_called()

    def test_each_diagnostic_is_reported_once(self):
        write_recording(self.folder, "bad", value=9000)
        messages = []
        snapshot = data.build_snapshot(self.editor, report=messages.append)
        self.assertEqual(messages, list(snapshot.notes))
        self.assertEqual(sum("DISABLED motion" in x for x in messages), 1)


class FakeScreen:
    def __init__(self, height=24, width=100, keys=()):
        self.height, self.width = height, width
        self.keys = iter(keys)
        self.lines = []
        self.erase()

    def getmaxyx(self): return self.height, self.width
    def keypad(self, enabled): pass
    def refresh(self): pass
    def erase(self): self.lines = [" " * self.width for _ in range(self.height)]
    def getch(self): return next(self.keys)
    def addnstr(self, y, x, text, length, attr=0):
        if y < 0 or y >= self.height or x < 0 or x+min(length, len(text)) > self.width:
            raise AssertionError("Draw outside screen")
        value = text[:length]
        self.lines[y] = self.lines[y][:x] + value + self.lines[y][x+len(value):]


class VisibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.view_path = self.folder / data.VIEW_FILENAME
        self.reference_path = self.folder / "comp_tray_reference.json"
        self.preset_path = self.folder / "_competition_presets.json"
        catalog = {str(i): (cq.Point(f"motion{i}", 1, point()),) for i in range(1, 81)}
        labels = {str(i): f"motion{i}" for i in range(1, 81)}
        labels["80"] = WIN
        aliases = {name: key for key, name in labels.items()}
        self.snapshot = data.CatalogSnapshot(catalog, labels, aliases,
            {"81": ("bad_motion", "Incomplete recording")}, (), self.folder / "motions")
        self.state = self.new_state()

    def new_state(self, snapshot=None):
        return menu.MenuState(snapshot or self.snapshot, quick.load_store(self.preset_path),
                              view_path=self.view_path, reference_path=self.reference_path)

    def test_all_entries_reachable_winning_line_first_and_no_hard_cap(self):
        s = self.state
        self.assertEqual(len(s.shown), 81)
        self.assertEqual(s.labels[s.shown[0]], WIN)
        seen = []
        for i in range(len(s.shown)):
            s.select.motion = i
            seen.append(s.selected_id)
        self.assertEqual(set(seen), set(s.labels))
        s.move_cursor(9999)
        self.assertEqual(s.selected_id, "81")
        s.move_cursor(-9999)
        self.assertEqual(s.selected_id, "80")

    def test_saved_visibility_uses_names_not_menu_ids(self):
        self.state.save_visibility({"motion1"})
        self.assertNotIn("1", self.new_state().shown)
        new_catalog = {"900": self.snapshot.catalog["1"]}
        changed = data.CatalogSnapshot(new_catalog, {"900": "motion1"},
                                       {"motion1": "900"}, {}, (), self.folder)
        self.assertEqual(self.new_state(changed).shown, [])

    def test_hidden_queue_macro_and_waypoints_unchanged(self):
        s = self.state
        s.add_selected()
        before = s.export_for_run()
        catalog = dict(s.catalog)
        s.save_visibility({WIN, "motion1"})
        self.assertEqual(s.export_for_run(), before)
        self.assertEqual(s.catalog, catalog)
        self.assertFalse(self.preset_path.exists())
        self.assertEqual(s.queue, [WIN])

    def test_all_toggle_reveals_hidden_without_changing_preferences(self):
        s = self.state
        s.save_visibility({WIN, "motion1"})
        before = self.view_path.read_bytes()
        s.toggle_all()
        self.assertEqual(len(s.shown), 81)
        s.toggle_all()
        self.assertEqual(len(s.shown), 79)
        self.assertEqual(self.view_path.read_bytes(), before)

    def test_winning_line_shortcut_reveals_hidden_lines(self):
        s = self.state
        s.save_visibility({WIN})
        s.toggle_winning_lines()
        self.assertEqual(s.shown, ["80"])
        s.add_selected()
        self.assertEqual(s.queue, [WIN])
        s.toggle_winning_lines()
        self.assertNotIn("80", s.shown)

    def test_checkbox_draft_is_reversible_and_cancel_does_not_save(self):
        draft = menu.VisibilityDraft(self.state)
        draft.toggle()
        self.assertIn(WIN, draft.hidden)
        self.assertEqual(self.state.hidden, set())
        draft.toggle()
        self.assertFalse(draft.dirty)
        self.assertFalse(self.view_path.exists())

    def test_bulk_hide_applies_only_to_filtered_names(self):
        draft = menu.VisibilityDraft(self.state)
        draft.winning_only = True
        draft.set_all(False)
        self.assertEqual(draft.hidden, {WIN})
        draft.set_all(True)
        self.assertEqual(draft.hidden, set())

    def test_blocked_entry_cannot_be_queued_even_if_checked(self):
        s = self.state
        s.select.motion = s.shown.index("81")
        with self.assertRaisesRegex(ValueError, "Incomplete recording"):
            s.add_selected()
        self.assertEqual(s.queue, [])

    def test_refresh_keeps_hidden_preferences_and_queue(self):
        s = self.state
        s.add_selected()
        s.save_visibility({"motion1"})
        s.refresh_catalog(self.snapshot)
        self.assertEqual(s.queue, [WIN])
        self.assertNotIn("1", s.shown)

    def test_render_minimum_terminal_has_range_and_every_entry_scrolls(self):
        screen = FakeScreen(18, 72)
        for index in (0, 30, 80):
            self.state.select.motion = index
            menu.draw(screen, self.state)
            text = "\n".join(screen.lines)
            self.assertIn("/81", text)
            self.assertIn("above", text)
            self.assertIn("below", text)
        self.assertIn("bad_motion", "\n".join(screen.lines))

    def test_ui_save_checkboxes_never_connects_or_starts_motion(self):
        screen = FakeScreen(keys=map(ord, "hn sq"))
        # H: open manager. N: hide all. Space: show first winning line. S: save. Q: exit.
        editor = SimpleNamespace(connect=Mock(side_effect=AssertionError("motion")))
        with ExitStack() as stack:
            for function in ("curs_set", "start_color", "use_default_colors", "init_pair"):
                stack.enter_context(patch.object(menu.curses, function))
            stack.enter_context(patch.object(menu, "build_snapshot", return_value=self.snapshot))
            result = menu.app(screen, editor, path=self.preset_path,
                              view_path=self.view_path, reference_path=self.reference_path)
        self.assertIsNone(result)
        editor.connect.assert_not_called()
        restored = self.new_state()
        self.assertEqual(restored.shown, ["80"])

    def test_reference_layout_still_has_no_effect_on_motion_catalog(self):
        s = self.state
        s.add_selected()
        before = s.export_for_run()
        s.edit_tray(block="empty", note="reference only")
        s.save_tray_reference()
        self.assertEqual(s.export_for_run(), before)
        self.assertEqual(s.hidden, set())

    def test_old_builder_returns_to_comp_with_queue_and_new_line(self):
        script = self.folder / "scripts" / "arm_board.py"
        script.parent.mkdir()
        script.write_text("# Stub; subprocess is mocked\n")
        state = self.state
        state.add_selected()
        state.change_speed(500, 25)
        new_name = "winning_line_c1r2_c2r2_c3r2"
        fresh = data.CatalogSnapshot(
            {**self.snapshot.catalog, "82": (cq.Point(new_name, 1, point()),)},
            {**self.snapshot.labels, "82": new_name},
            {**self.snapshot.aliases, new_name: "82"},
            self.snapshot.unavailable, (), self.snapshot.folder)
        editor = SimpleNamespace(connect=Mock(side_effect=AssertionError("motion")))
        with ExitStack() as stack:
            for fn in ("def_prog_mode", "endwin", "reset_prog_mode", "curs_set"):
                stack.enter_context(patch.object(menu.curses, fn))
            runner = stack.enter_context(patch.object(menu.subprocess, "run",
                return_value=SimpleNamespace(returncode=0)))
            stack.enter_context(patch.object(menu, "build_snapshot", return_value=fresh))
            menu.open_board_tool(FakeScreen(), editor, state, "builder")
        self.assertEqual(runner.call_args.args[0], [sys.executable, str(script),
            "--repo", str(self.folder), "--terminal", "--queue", "--speed", "500",
            "--acceleration", "25"])
        self.assertEqual(state.queue, [WIN])
        self.assertIn("82", state.shown)
        self.assertTrue(state.winning_only)
        self.assertFalse(self.preset_path.exists())
        editor.connect.assert_not_called()

    def test_pickup_mapping_uses_offline_tool_and_keeps_queue(self):
        script = self.folder / "scripts" / "arm_board.py"
        script.parent.mkdir()
        script.write_text("# Stub; subprocess is mocked\n")
        self.state.add_selected()
        editor = SimpleNamespace(connect=Mock(side_effect=AssertionError("motion")))
        with ExitStack() as stack:
            for fn in ("def_prog_mode", "endwin", "reset_prog_mode", "curs_set"):
                stack.enter_context(patch.object(menu.curses, fn))
            runner = stack.enter_context(patch.object(menu.subprocess, "run",
                return_value=SimpleNamespace(returncode=0)))
            stack.enter_context(patch.object(menu, "build_snapshot", return_value=self.snapshot))
            menu.open_board_tool(FakeScreen(), editor, self.state, "mapping")
        self.assertEqual(runner.call_args.args[0], [sys.executable, str(script),
            "--repo", str(self.folder), "--remap"])
        self.assertEqual(self.state.queue, [WIN])
        self.assertFalse(self.state.winning_only)
        editor.connect.assert_not_called()

    def test_failed_tool_launch_restores_terminal_and_preserves_queue(self):
        script = self.folder / "scripts" / "arm_board.py"
        script.parent.mkdir()
        script.write_text("# Stub; subprocess is mocked\n")
        self.state.add_selected()
        with ExitStack() as stack:
            for fn in ("def_prog_mode", "endwin", "curs_set"):
                stack.enter_context(patch.object(menu.curses, fn))
            restore = stack.enter_context(patch.object(menu.curses, "reset_prog_mode"))
            stack.enter_context(patch.object(menu.subprocess, "run",
                side_effect=OSError("Could not start child")))
            with self.assertRaisesRegex(OSError, "Could not start"):
                menu.open_board_tool(FakeScreen(), object(), self.state, "builder")
        restore.assert_called_once()
        self.assertEqual(self.state.queue, [WIN])

    def test_comp_keys_open_existing_tools_without_running_queue(self):
        screen = FakeScreen(keys=map(ord, "bmq"))
        editor = SimpleNamespace(connect=Mock(side_effect=AssertionError("motion")))
        with ExitStack() as stack:
            for fn in ("curs_set", "start_color", "use_default_colors", "init_pair"):
                stack.enter_context(patch.object(menu.curses, fn))
            stack.enter_context(patch.object(menu, "build_snapshot", return_value=self.snapshot))
            opener = stack.enter_context(patch.object(menu, "open_board_tool"))
            result = menu.app(screen, editor, path=self.preset_path,
                view_path=self.view_path, reference_path=self.reference_path)
        self.assertIsNone(result)
        self.assertEqual([call.args[-1] for call in opener.call_args_list],
                         ["builder", "mapping"])
        editor.connect.assert_not_called()


class StorageTests(unittest.TestCase):
    def test_save_validation_and_backup(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / data.VIEW_FILENAME
            data.save_hidden({"GRAB1"}, path)
            self.assertEqual(data.load_hidden(path), {"grab1"})
            data.save_hidden({WIN}, path)
            self.assertEqual(len(list(path.parent.glob("*.bak"))), 1)
            with self.assertRaises(ValueError):
                data.save_hidden({"bad/name"}, path)
            with self.assertRaises(ValueError):
                data.save_hidden({"grab1"}, path.with_name("tray_mapping.json"))
            self.assertEqual(data.load_hidden(path), {WIN})

    def test_viewport_covers_arbitrary_lengths(self):
        for total in (0, 1, 9, 100, 1000):
            for height in (1, 5, 24):
                for index in range(total):
                    start, end = data.window_bounds(index, total, height)
                    self.assertTrue(start <= index < end <= total)
                    self.assertLessEqual(end-start, height)
                if total == 0:
                    self.assertEqual(data.window_bounds(0, total, height), (0, 0))


if __name__ == "__main__":
    unittest.main()
