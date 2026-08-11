#!/usr/bin/env python3
import unittest

from c2_2_bayes import (
    FACTOR_INDICATORS,
    WindowObservation,
    calibration_snapshot,
    direction,
    factor_posterior,
    independent_confidence,
    metric_z,
    observation_weight,
    posterior_update,
    robust_prior,
    total_evidence_score,
)


class C22BayesTests(unittest.TestCase):
    def test_metric_preprocessing_is_directional_and_winsorized(self):
        positive = metric_z(100, [1, 2, 3, 100, 100000], kind="nonnegative", direction="positive")
        negative = metric_z(100, [1, 2, 3, 100, 100000], kind="nonnegative", direction="negative")
        self.assertIsNotNone(positive)
        self.assertIsNotNone(negative)
        self.assertAlmostEqual(positive, -negative, places=12)

    def test_prior_uses_robust_mad_and_fallback_is_neutral(self):
        prior = robust_prior([-2, -1, 0, 1, 2])
        self.assertEqual(prior.source, "cohort")
        self.assertAlmostEqual(prior.mu, 0.0)
        self.assertEqual(prior.tau, 1.4826)
        fallback = robust_prior([10, 20], fallback=True)
        self.assertEqual((fallback.mu, fallback.tau, fallback.source), (0.0, 1.0, "fallback"))

    def test_source_weights_and_invalid_statuses(self):
        self.assertEqual(observation_weight("healthy", independent_source_count=2), 1.0)
        self.assertEqual(observation_weight("single_source", independent_source_count=1), 0.5)
        self.assertEqual(observation_weight("quota_limited", independent_source_count=2), 0.0)
        self.assertEqual(observation_weight("healthy", comparable=False), 0.0)

    def test_posterior_is_idempotent_for_duplicate_window(self):
        prior = robust_prior([])
        observations = [
            WindowObservation("asset|metric|window|rule", 2.0, 1.0),
            WindowObservation("asset|metric|window|rule", -20.0, 0.5),
            WindowObservation("asset|metric|window-2|rule", 1.0, 0.5),
        ]
        result = posterior_update(prior, observations)
        self.assertEqual(result.measured_observations, 2)
        self.assertEqual(result.duplicate_observations, 1)
        repeated = posterior_update(prior, observations + observations)
        self.assertEqual(result.mean, repeated.mean)
        self.assertEqual(result.variance, repeated.variance)

    def test_factor_keeps_missing_indicator_neutral_and_total_weights_fixed(self):
        prior = robust_prior([])
        measured = posterior_update(prior, [WindowObservation("one", 3.0, 1.0)])
        neutral = posterior_update(prior, [])
        indicators = {name: (measured if name == "volume" else neutral) for name in FACTOR_INDICATORS["D"]}
        factor = factor_posterior("D", indicators)
        self.assertEqual(factor.indicator_count, 4)
        self.assertEqual(factor.measured_indicator_count, 1)
        self.assertNotEqual(factor.posterior.score, measured.score)
        factors = {name: factor_posterior(name, {indicator: neutral for indicator in indicators_for}) for name, indicators_for in FACTOR_INDICATORS.items()}
        total = total_evidence_score(factors)
        self.assertAlmostEqual(total.score, 50.0, places=12)

    def test_direction_uses_probability_thresholds_not_raw_delta(self):
        prior = robust_prior([])
        previous = posterior_update(prior, [WindowObservation("old", 0.0, 0.5)])
        current = posterior_update(prior, [WindowObservation("new", 5.0, 1.0)])
        self.assertEqual(direction(current, previous), "improving")
        self.assertEqual(direction(posterior_update(prior, []), previous), "no_measured")

    def test_confidence_is_independent_and_calibration_never_auto_tunes(self):
        self.assertAlmostEqual(independent_confidence(100, 80, 50, 40), 73.0)
        summary = calibration_snapshot(29, retained=4, liquidity_guardrail_passed=3, risk_found=1)
        self.assertEqual(summary["status"], "sample_insufficient")
        self.assertFalse(summary["usesReturnAsLabel"])
        self.assertFalse(summary["autoTuning"])


if __name__ == "__main__":
    unittest.main()
