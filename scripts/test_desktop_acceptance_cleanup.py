from __future__ import annotations

import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


class DesktopAcceptanceCleanupTests(unittest.TestCase):
    def test_failure_path_restores_process_and_port_baseline(self) -> None:
        completed = subprocess.run(
            (
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(SCRIPTS / "test-desktop-acceptance-cleanup.ps1"),
            ),
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8-sig",
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        result = json.loads(completed.stdout)
        self.assertEqual(result["status"], "passed")
        self.assertTrue(result["intentionalFailureCleanup"])
        self.assertTrue(result["desktopStateCompared"])
        self.assertTrue(result["webView2StateCompared"])
        self.assertTrue(result["trackedProcessesStopped"])

    def test_current_desktop_acceptance_uses_mandatory_finally_guard(self) -> None:
        smoke = (SCRIPTS / "test-c2.3-desktop-smoke.ps1").read_text(encoding="utf-8-sig")
        lifecycle = (SCRIPTS / "test-c2.3-independent-lifecycle.ps1").read_text(encoding="utf-8-sig")
        for source in (smoke, lifecycle):
            self.assertIn("desktop-acceptance-guard.ps1", source)
            self.assertIn("finally", source)
            self.assertIn("Complete-DesktopAcceptanceGuard", source)
        self.assertNotIn("$candidatePid = 4160", lifecycle)
        self.assertIn("will not replace a pre-existing service", lifecycle)

    def test_legacy_desktop_tests_cannot_silently_leave_their_resources(self) -> None:
        cold_start = (SCRIPTS / "test-convexity-cold-start.ps1").read_text(encoding="utf-8-sig")
        window = (SCRIPTS / "test-convexity-window-integration.ps1").read_text(encoding="utf-8-sig")
        for source in (cold_start, window):
            self.assertIn("desktop-acceptance-guard.ps1", source)
            self.assertIn("Complete-DesktopAcceptanceGuard", source)
        self.assertIn("the test made no changes", cold_start)
        self.assertIn("will not close a user-owned desktop", window)


if __name__ == "__main__":
    unittest.main()
