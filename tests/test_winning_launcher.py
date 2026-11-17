"""Winning-line shortcuts open the existing builder without starting motion."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import arm_board
import arm_board_terminal as terminal


class WinningLauncherTests(unittest.TestCase):
    def test_queue_requires_terminal_before_loading_arm_code(self):
        with patch.object(sys, "argv", ["arm_board.py", "--queue"]):
            with patch.object(arm_board, "load_script6") as load:
                with patch("sys.stderr", new=io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        arm_board.main()
        self.assertEqual(error.exception.code, 2)
        load.assert_not_called()

    def test_cli_passes_requested_initial_mode_to_terminal(self):
        for queue in (False, True):
            with self.subTest(queue=queue):
                argv = ["arm_board.py", "--terminal"] + (["--queue"] if queue else [])
                with patch.object(sys, "argv", argv):
                    with patch.object(arm_board, "load_script6", return_value=SimpleNamespace()):
                        with patch.object(arm_board, "load_mapping", return_value={"slots": []}):
                            with patch.object(terminal, "run_terminal") as run:
                                arm_board.main()
                run.assert_called_once()
                self.assertEqual(run.call_args.kwargs["start_queue"], queue)

    def test_startup_preserves_preview_in_board_and_queue_modes(self):
        for queue, expected_mode in ((False, "place"), (True, "queue")):
            with self.subTest(queue=queue):
                screen = Mock()
                screen.getch.side_effect = [ord("q")]
                play = Mock()
                callbacks = dict(compose_queue=Mock(), export_winning_line=Mock(),
                                 sync_winning_lines=Mock(), link_winning_line=Mock())
                with patch.object(terminal.curses, "curs_set"):
                    with patch.object(terminal.curses, "set_escdelay"):
                        with patch.object(terminal, "draw") as draw:
                            with patch.object(terminal.curses, "wrapper",
                                              side_effect=lambda fn, *args: fn(screen, *args)):
                                terminal.run_terminal(Mock(), play, start_queue=queue, **callbacks)
                self.assertEqual(draw.call_args.args[2], expected_mode)
                self.assertIs(draw.call_args.args[3], False)
                play.assert_not_called()
                for callback in callbacks.values():
                    callback.assert_not_called()

    def test_shell_shortcuts_dispatch_queue_without_changing_board_shortcut(self):
        with TemporaryDirectory() as directory:
            project = Path(directory)
            (project / "scripts").mkdir()
            (project / "arm_settings.py").touch()
            (project / "scripts/05_waypoint_sequence.py").touch()
            (project / "scripts/arm_board.py").write_text(
                "import json, sys\nprint(json.dumps(sys.argv[1:]))\n")
            for command in ("W", "wins", "win", "winning", "board", "B"):
                with self.subTest(command=command):
                    result = subprocess.run(
                        ["bash", str(ROOT / "ARM_MENU.sh"), command, "--speed", "500"],
                        env=dict(os.environ, ARM_PROJECT_DIR=str(project)),
                        check=True, capture_output=True, text=True,
                    )
                    actual = json.loads(result.stdout.splitlines()[-1])
                    expected = ["--terminal"]
                    if command not in ("board", "B"):
                        expected.append("--queue")
                    expected.extend(["--speed", "500"])
                    self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
