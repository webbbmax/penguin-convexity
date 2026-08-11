#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

from run_real_case_calibration import build_calibration_snapshot


PROJECT_ROOT = Path(__file__).resolve().parent.parent
CASES_PATH = PROJECT_ROOT / "fixtures" / "real-historical-cases-v1.json"


class RealCaseCalibrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = json.loads(CASES_PATH.read_text(encoding="utf-8"))
        cls.snapshot = build_calibration_snapshot()

    def test_exactly_twenty_unique_cases(self):
        case_ids = [case["caseId"] for case in self.fixtures["cases"]]
        self.assertEqual(20, len(case_ids))
        self.assertEqual(20, len(set(case_ids)))

    def test_every_case_has_traceable_source(self):
        for case in self.fixtures["cases"]:
            self.assertTrue(case["sources"], case["caseId"])
            for source in case["sources"]:
                self.assertTrue(source["url"].startswith("https://"))
                self.assertTrue(source["publishedAt"])
                self.assertIn(
                    source["factBoundary"],
                    {
                        "confirmed_fact",
                        "project_claim",
                        "regulator_allegation",
                        "court_confirmed",
                        "legal_record",
                    },
                )

    def test_allegations_are_not_labeled_confirmed(self):
        allegation_cases = {"real-terra-collapse", "real-celsius-withdrawal-halt", "real-safemoon-liquidity", "real-mango-manipulation"}
        for case in self.fixtures["cases"]:
            if case["caseId"] in allegation_cases:
                self.assertTrue(
                    any(
                        source["factBoundary"] == "regulator_allegation"
                        for source in case["sources"]
                    )
                )

    def test_market_data_never_replaces_event_evidence(self):
        for case in self.fixtures["cases"]:
            self.assertTrue(case["sources"], case["caseId"])
            self.assertNotEqual("market_data", case["sources"][0]["factBoundary"])

    def test_calibration_matches_expected_rules(self):
        failures = [
            (item["caseId"], item["expectedSequence"], item["actualSequence"])
            for item in self.snapshot["results"]
            if not item["passed"]
        ]
        self.assertEqual([], failures)
        self.assertEqual(20, self.snapshot["summary"]["primaryEvidenceCaseCount"])

    def test_clickable_page_contains_core_controls(self):
        html = (PROJECT_ROOT / "app" / "real-case-calibration.html").read_text(encoding="utf-8")
        javascript = (PROJECT_ROOT / "app" / "real-case-calibration.js").read_text(encoding="utf-8")
        self.assertIn("20 个真实正反案例校准", html)
        self.assertIn("realCaseTypeFilter", html)
        self.assertIn("marketStatusFilter", html)
        self.assertIn("data-real-case-id", javascript)
        self.assertIn("打开原始证据", javascript)

    def test_all_convexity_pages_link_to_real_calibration(self):
        for filename in ("data-dictionary.html", "rules-replay.html"):
            html = (PROJECT_ROOT / "app" / filename).read_text(encoding="utf-8")
            self.assertIn("real-case-calibration.html", html)


if __name__ == "__main__":
    unittest.main()
