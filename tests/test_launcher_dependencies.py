"""Ensure one-click deployment includes every local Python import it needs."""
import ast
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INSTALLER = ROOT / "ONE_CLICK_COMPETITION.sh"


class DeploymentManifestChecks(unittest.TestCase):
    def test_deployed_modules_cover_local_imports(self):
        shell = INSTALLER.read_text()
        match = re.search(r"^scripts=\(\n(.*?)^\)", shell, re.M | re.S)
        self.assertIsNotNone(match, "Installer deployment list not found")
        included = set(re.findall(r"^\s+([A-Za-z_][A-Za-z0-9_]*\.py)\s*$",
                                  match.group(1), re.M))
        self.assertIn("06_live_grid_tool.py", included)
        self.assertIn("arm_board.py", included)
        self.assertIn("tray_remap.py", included)
        for name in included:
            path = SCRIPTS / name
            self.assertTrue(path.is_file(), f"Missing deploy source {name}")
            parsed = ast.parse(path.read_text(), filename=str(path))
            imports = set()
            for node in ast.walk(parsed):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imports.add(node.module.split(".")[0])
                elif isinstance(node, ast.Import):
                    imports.update(alias.name.split(".")[0] for alias in node.names)
            for module in imports:
                local = SCRIPTS / f"{module}.py"
                if local.is_file():
                    self.assertIn(local.name, included,
                                  f"{name} imports local module {module} but installer omits it")


if __name__ == "__main__":
    unittest.main()
