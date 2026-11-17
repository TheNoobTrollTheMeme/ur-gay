"""Winning-line menu checks: no robot/display required and no recording writes."""
from copy import deepcopy
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import competition_quick as quick
import competition_tui as tui


class Screen:
    """Small deterministic curses stand-in, with clipping like addnstr."""
    def __init__(self, height=18, width=72, keys=()):
        self.height, self.width = height, width
        self.keys = iter(keys)
        self.frames = []
        self.erase()

    def erase(self):
        self.lines = [[' '] * self.width for _ in range(self.height)]

    def getmaxyx(self):
        return self.height, self.width

    def addnstr(self, y, x, text, count, attr=0):
        assert 0 <= y < self.height and 0 <= x < self.width
        for offset, char in enumerate(str(text)[:min(count, self.width-x)]):
            self.lines[y][x+offset] = char

    def refresh(self):
        self.frames.append(self.text())

    def text(self):
        return '\n'.join(''.join(row).rstrip() for row in self.lines)

    def getch(self):
        return next(self.keys)

    def keypad(self, enabled):
        pass


class WinningMenuTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / '_competition_presets.json'
        self.ref = Path(self.temp.name) / 'comp_tray_reference.json'
        self.line = 'winning_line_c1r1_c2r1_c3r1'
        self.reverse = 'winning_line_c3r1_c2r1_c1r1_2'
        self.catalog = {
            '2': ({'shoulder': 2000},),
            '10': ({'shoulder': 2010}, {'shoulder': 2020}),
            '34': tuple({'shoulder': 2100 + i} for i in range(18)),
            '90': tuple({'shoulder': 2200 + i} for i in range(18)),
        }
        self.labels = {'2': 'grab1', '10': 'c1r1',
                       '34': self.line, '90': self.reverse}
        self.aliases = {name: ident for ident, name in self.labels.items()}
        self.state = tui.CompetitionState(
            self.catalog, self.labels, self.aliases, quick.load_store(self.path),
            reference_path=self.ref)

    def test_lines_are_visible_first_without_search_or_changed_ids(self):
        self.assertEqual(self.state.filter_text, '')
        self.assertEqual(self.state.shown, ['34', '90', '2', '10'])
        rows = tui.motion_menu_rows(self.state)
        self.assertEqual(rows[0], (None, 'WINNING LINES (2)'))
        self.assertEqual(rows[1], ('34', 'WIN C1R1>C2R1>C3R1'))
        self.assertEqual(rows[2], ('90', 'WIN C3R1>C2R1>C1R1 #2'))
        self.assertEqual(rows[3], (None, 'OTHER MOTIONS (2)'))
        self.assertEqual(self.labels['34'], self.line)

    def test_enter_queues_whole_export_not_individual_cells(self):
        before = deepcopy(self.catalog)
        self.state.add_selected()
        self.assertEqual(self.state.queue, [self.line])
        plan, names = quick.resolve_queue(self.state.queue, self.catalog,
                                         self.labels, self.aliases)
        ident = list(plan.pending)[0]
        self.assertEqual(ident, '34')
        self.assertEqual(len(plan.catalog[ident]), 18)
        self.assertEqual(names, (self.line,))
        self.assertEqual(self.catalog, before)
        self.assertFalse(self.path.exists())
        self.assertFalse(self.ref.exists())

    def test_w_toggles_left_pane_without_touching_queue_or_speed(self):
        self.state.add_selected()
        self.state.select.pane = 1
        self.state.set_filter('grab')
        self.state.toggle_winning_lines()
        self.assertTrue(self.state.winning_only)
        self.assertEqual(self.state.shown, ['34', '90'])
        self.assertEqual(self.state.select.pane, 0)
        self.assertEqual(self.state.select.motion, 0)
        self.assertEqual(self.state.filter_text, '')
        self.state.toggle_winning_lines()
        self.assertEqual(self.state.shown, ['34', '90', '2', '10'])
        self.assertEqual(self.state.queue, [self.line])
        self.assertEqual((self.state.speed, self.state.accel), (300, 20))

    def test_search_matches_original_filename_and_compact_label(self):
        self.state.set_filter('winning_line_c1r1')
        self.assertEqual(self.state.shown, ['34'])
        self.state.set_filter('C3R1>C2R1>C1R1')
        self.assertEqual(self.state.shown, ['90'])
        self.state.set_filter('grab')
        self.assertEqual(self.state.shown, ['2'])

    def test_headers_are_not_focusable_and_scroll_keeps_selection(self):
        for expected in ('34', '90', '2', '10'):
            rows, selected = tui.motion_menu_window(self.state, 2)
            self.assertEqual(selected, expected)
            self.assertIn(expected, [identifier for identifier, _ in rows])
            self.assertEqual(len(rows), 2)
            self.state.move_cursor(1)

    def test_render_at_minimum_terminal_size_shows_cells_and_controls(self):
        screen = Screen()
        with patch.object(tui.curses, 'color_pair', return_value=0):
            tui.draw(screen, self.state)
        text = screen.text()
        self.assertIn('WINNING LINES (2)', text)
        self.assertIn('WIN C1R1>C2R1>C3R1', text)
        self.assertIn('WIN C3R1>C2R1>C1R1 #2', text)
        self.assertIn('OTHER MOTIONS (2)', text)
        self.assertIn('W win lines', text)
        self.assertIn('Selected: ' + self.line, text)
        self.assertIn('grab1', text)

    def test_no_ready_winning_lines_is_empty_not_synthetic_motion(self):
        del self.state.catalog['34']
        del self.state.catalog['90']
        self.assertEqual(self.state.shown, ['2', '10'])
        self.state.toggle_winning_lines()
        self.assertEqual(self.state.shown, [])
        rows, selected = tui.motion_menu_window(self.state, 8)
        self.assertIsNone(selected)
        self.assertIn((None, '(no matching READY lines)'), rows)
        with self.assertRaisesRegex(ValueError, 'No matching READY'):
            self.state.add_selected()
        self.assertEqual(self.state.queue, [])

    def test_real_w_key_and_enter_stage_line_without_connecting(self):
        # W, Enter, Quit edits the queue but never returns a run request.
        screen = Screen(keys=(ord('w'), 10, ord('q')))
        editor = SimpleNamespace(connect=lambda: self.fail('Unexpected motor connection'))
        with patch.object(quick, 'catalog_for', return_value=(
                self.catalog, self.labels, self.aliases)), \
             patch.object(tui.curses, 'curs_set'), \
             patch.object(tui.curses, 'start_color'), \
             patch.object(tui.curses, 'use_default_colors'), \
             patch.object(tui.curses, 'init_pair'), \
             patch.object(tui.curses, 'color_pair', return_value=0):
            result = tui.app(screen, editor, path=self.path,
                             reference_path=self.ref)
        self.assertIsNone(result)
        self.assertIn('[ WINNING LINES ]', screen.frames[1])
        self.assertIn('1 queued', screen.frames[-1])
        self.assertFalse(self.path.exists())
        self.assertFalse(self.ref.exists())

    def test_new_catalog_lines_join_the_display_on_refresh(self):
        self.state.catalog['101'] = ({'shoulder': 2300},)
        self.state.labels['101'] = 'winning_line_c1r1_c2r2_c3r3'
        self.assertEqual(self.state.shown, ['34', '90', '101', '2', '10'])
        self.state.select.motion = 2
        self.assertEqual(tui.motion_menu_window(self.state, 8)[1], '101')

    def test_nonwinning_catalog_order_is_unchanged(self):
        self.state.catalog = {n: self.catalog[n] for n in ('10', '2')}
        self.assertEqual(self.state.shown, ['2', '10'])
        self.assertEqual(tui.motion_label('grab1'), 'grab1')
        self.assertEqual(tui.motion_label('winning_line_custom'), 'winning_line_custom')


if __name__ == '__main__':
    unittest.main()
