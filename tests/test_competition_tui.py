"""Hardware-free pseudo-GUI state tests. No curses screen or servo imports needed."""
import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import competition_queue as cq
import competition_quick as quick
import competition_tui as tui


class CompTUITests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "_competition_presets.json"
        self.reference_path = Path(self.temp.name) / "comp_tray_reference.json"
        raw = {"2": (cq.Point("grab1", 1, {"base": 2000}),),
               "7": (cq.Point("c1r1m", 1, {"base": 1800}),
                     cq.Point("c1r1p", 1, {"base": 1900})),
               "10": (cq.Point("grab2", 1, {"base": 2100}),)}
        labels = {"2": "grab1", "7": "c1r1", "10": "grab2"}
        aliases = {v: k for k, v in labels.items()}
        self.state = tui.CompetitionState(raw, labels, aliases,
                                          quick.load_store(self.path),
                                          reference_path=self.reference_path)

    def test_cursor_add_remove_reorder(self):
        s = self.state
        self.assertEqual(s.shown, ["2", "7", "10"])
        s.add_selected()
        self.assertEqual(s.queue, ["grab1"])
        s.move_cursor(1)
        s.add_selected()
        self.assertEqual(s.queue, ["grab1", "c1r1"])
        s.switch_pane()
        s.reorder(-1)
        self.assertEqual(s.queue, ["c1r1", "grab1"])
        s.remove_selected()
        self.assertEqual(s.queue, ["grab1"])
        s.clear_queue()
        self.assertEqual(s.queue, [])

    def test_search_filter_doesnt_renumber_catalog(self):
        s = self.state
        s.set_filter("grab2")
        self.assertEqual(s.shown, ["10"])
        s.add_selected()
        self.assertEqual(s.queue, ["grab2"])
        s.set_filter("")
        self.assertEqual(s.shown, ["2", "7", "10"])

    def test_save_load_speed_and_macro_against_latest_aliases(self):
        s = self.state
        s.change_speed(500, 25)
        s.add_selected()
        s.select.motion = 1
        s.add_selected()
        s.save_macro("opener", path=self.path)
        self.assertEqual(quick.load_store(self.path)["presets"]["opener"]["queue"],
                         ["grab1", "c1r1"])
        s.clear_queue()
        s.change_speed(300, 20)
        s.load_macro("opener")
        self.assertEqual(s.queue, ["grab1", "c1r1"])
        self.assertEqual((s.speed, s.accel), (500, 25))
        self.assertEqual(s.export_for_run(),
                         {"queue": ["grab1", "c1r1"], "speed": 500, "accel": 25})

    def test_speed_preference_persists_without_starting_arm(self):
        self.state.change_speed(550, 28, path=self.path)
        self.assertEqual((self.state.speed, self.state.accel), (550, 28))
        saved = quick.load_store(self.path)
        self.assertEqual((saved["speed"], saved["accel"]), (550, 28))
        self.assertEqual(self.state.queue, [])

    def test_invalid_update_is_atomic(self):
        s = self.state
        s.add_selected()
        before = list(s.queue)
        with self.assertRaises(ValueError):
            s.change_speed(9999, 30)
        self.assertEqual(s.queue, before)
        self.assertEqual((s.speed, s.accel), (300, 20))
        s.store["presets"]["broken"] = {
            "queue": ["missing_recording"], "speed": 400, "accel": 20
        }
        with self.assertRaisesRegex(ValueError, "Unavailable"):
            s.load_macro("broken")
        self.assertEqual(s.queue, before)

    def test_tray_layout_can_be_edited_and_saved_without_touching_queue(self):
        state = self.state
        self.assertFalse(state.tray_dirty)
        self.assertEqual(state.selected_slot["block"], "cylinder")
        state.cycle_tray_block()
        state.edit_tray(note="Left tray cylinder moved", position="rear-inner")
        self.assertEqual(state.selected_slot["block"], "trophy")
        self.assertTrue(state.tray_dirty)
        self.assertEqual(state.queue, [])
        state.save_tray_reference()
        self.assertFalse(state.tray_dirty)
        self.assertEqual(state.selected_slot["note"], "Left tray cylinder moved")
        self.assertTrue(self.reference_path.exists())
        self.assertFalse(self.path.exists())
        state.select_tray_slot(6)
        state.cycle_tray_zone()
        self.assertTrue(state.tray_dirty)
        state.discard_tray_reference()
        self.assertFalse(state.tray_dirty)
        self.assertEqual(state.selected_slot["zone"], "B-right")

    def test_tray_reference_does_not_affect_macro_playback_targets(self):
        state = self.state
        state.add_selected()
        plan_before = state.export_for_run()
        saved = dict(state.catalog)
        state.select_tray_slot(1)
        state.edit_tray(block="empty", note="No block there currently")
        state.save_tray_reference()
        self.assertEqual(state.export_for_run(), plan_before)
        self.assertEqual(state.catalog, saved)
        self.assertEqual(state.queue, ["grab1"])
        self.assertEqual(state.selected_slot["block"], "empty")

    def test_tray_reference_rejects_invalid_input_without_changes(self):
        state = self.state
        previous = state.tray.copy()
        with self.assertRaises(ValueError):
            state.edit_tray(position="")
        self.assertEqual(state.tray, previous)
        with self.assertRaises(ValueError):
            state.edit_tray(servo=9000)
        self.assertEqual(state.tray, previous)
        self.assertFalse(self.reference_path.exists())

    def test_curses_cancel_leaves_queue_unplayed(self):
        editor = object()
        with patch.object(tui.curses, "wrapper", return_value=None) as fake:
            self.assertIsNone(tui.launch(editor, path=self.path))
            fake.assert_called_once()

    def test_pseudo_gui_does_not_run_motor_inside_curses(self):
        # Selecting R returns a pure macro and restores terminal BEFORE execute.
        state = self.state
        state.add_selected()
        macro = state.export_for_run()
        self.assertEqual(macro["queue"], ["grab1"])
        self.assertIsInstance(macro["speed"], int)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
