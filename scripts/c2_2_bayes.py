"""C2.2 deterministic hierarchical empirical-Bayes evidence calculations.

This module is deliberately independent of the database, web server and page
clock.  It implements only the frozen C2.2 Bayes specification; C2.1 hard
gates and the four strong evidence paths remain outside this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from math import erf, exp, isfinite, log, sqrt
from statistics import median
from typing import Any, Iterable, Mapping, Sequence


EPSILON = 1e-12
P_LOWER = 0.01
P_UPPER = 0.99
NORMAL_80_Z = 1.2815515655446004
INVALID_STATUSES = {
    "no_data",
    "quota_limited",
    "source_failure",
    "unsupported",
    "configuration_missing",
    "program_failure",
    "conflict",
    "unit_conflict",
    "incomparable",
    "expired",
}

FACTOR_WEIGHTS = {
    "D": 0.25,
    "L": 0.25,
    "S": 0.20,
    "G": 0.15,
    "Q": 0.15,
}

FACTOR_INDICATORS = {
    "D": ("volume", "trade_count", "volume_liquidity", "real_buy_sell"),
    "L": ("relative_liquidity", "standard_sell_loss_inverse", "liquidity_retention"),
    "S": ("top10_concentration_inverse", "hhi_inverse", "net_supply_reduction"),
    "G": ("product_usage_growth", "relative_expansion", "risk_adjusted_remaining"),
    "Q": ("severe_anomaly_inverse", "cross_source_consistency", "activity_concentration_inverse"),
}

METRIC_KINDS = {"nonnegative", "proportion", "raw"}
DIRECTIONS = {"positive", "negative"}


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def clamp_probability(value: float) -> float:
    return min(P_UPPER, max(P_LOWER, float(value)))


def logistic(value: float) -> float:
    """Stable logistic function used for posterior scores."""

    value = float(value)
    if value >= 0:
        e = exp(-value)
        return 1.0 / (1.0 + e)
    e = exp(value)
    return e / (1.0 + e)


def logit(value: float) -> float:
    probability = clamp_probability(value)
    return log(probability / (1.0 - probability))


def _transform_raw(value: float, kind: str) -> float:
    if kind not in METRIC_KINDS:
        raise ValueError(f"unsupported metric kind: {kind}")
    if kind == "proportion":
        return logit(value)
    if kind == "nonnegative":
        # Negative amounts are invalid rather than silently converted.
        if value < 0:
            raise ValueError("nonnegative metric cannot be negative")
        return log(1.0 + value)
    return value


def winsor_bounds(values: Sequence[float]) -> tuple[float, float]:
    """Return deterministic P1/P99 bounds using linear interpolation."""

    clean = sorted(float(v) for v in values if _finite(v) is not None)
    if not clean:
        raise ValueError("at least one finite value is required")
    if len(clean) == 1:
        return clean[0], clean[0]

    def quantile(probability: float) -> float:
        position = (len(clean) - 1) * probability
        lower = int(position)
        upper = min(len(clean) - 1, lower + 1)
        fraction = position - lower
        return clean[lower] + (clean[upper] - clean[lower]) * fraction

    return quantile(0.01), quantile(0.99)


def winsorize(value: float, lower: float, upper: float) -> float:
    if lower > upper:
        raise ValueError("winsor lower bound must not exceed upper bound")
    return min(upper, max(lower, float(value)))


def midrank_percentile(value: float, cohort: Sequence[float]) -> float:
    """Return a deterministic midrank percentile in (0, 1).

    Ties receive their average rank.  A one-element cohort is neutral (0.5),
    so a single object never becomes apparent evidence by itself.
    """

    clean = sorted(float(v) for v in cohort if _finite(v) is not None)
    if not clean:
        raise ValueError("cohort must contain a finite value")
    if len(clean) == 1:
        return 0.5
    less = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return (less + (equal / 2.0)) / len(clean)


def metric_z(value: Any, cohort: Sequence[Any], *, kind: str = "nonnegative", direction: str = "positive") -> float | None:
    """Transform a raw metric into a signed cohort percentile z-score."""

    raw = _finite(value)
    if raw is None or direction not in DIRECTIONS:
        return None
    transformed: list[float] = []
    for candidate in cohort:
        item = _finite(candidate)
        if item is None:
            continue
        try:
            transformed.append(_transform_raw(item, kind))
        except ValueError:
            continue
    if not transformed:
        return None
    try:
        current = _transform_raw(raw, kind)
    except ValueError:
        return None
    lower, upper = winsor_bounds(transformed)
    current = winsorize(current, lower, upper)
    clipped_cohort = [winsorize(item, lower, upper) for item in transformed]
    percentile = midrank_percentile(current, clipped_cohort)
    if direction == "negative":
        percentile = 1.0 - percentile
    return logit(percentile)


@dataclass(frozen=True)
class Prior:
    mu: float
    tau: float
    source: str
    sample_size: int

    @property
    def variance(self) -> float:
        return self.tau * self.tau


def robust_prior(cohort_z: Sequence[float], *, fallback: bool = False) -> Prior:
    """Build the frozen robust prior from a cohort or the neutral fallback."""

    clean = sorted(float(value) for value in cohort_z if _finite(value) is not None)
    if fallback or not clean:
        return Prior(mu=0.0, tau=1.0, source="fallback", sample_size=0)
    center = float(median(clean))
    deviations = sorted(abs(value - center) for value in clean)
    mad = float(median(deviations))
    tau = max(1.4826 * mad, 0.50)
    return Prior(mu=center, tau=tau, source="cohort", sample_size=len(clean))


@dataclass(frozen=True)
class WindowObservation:
    unique_key: str
    z: float
    weight: float
    status: str = "healthy"


def observation_weight(
    status: str,
    *,
    independent_source_count: int = 0,
    comparable: bool = True,
    conflict: bool = False,
) -> float:
    """Map source/window health to the only three allowed evidence weights."""

    normalized = str(status or "").strip().lower()
    if not comparable or conflict or normalized in INVALID_STATUSES:
        return 0.0
    if independent_source_count >= 2 and normalized in {"healthy", "ok", "complete", "success"}:
        return 1.0
    if normalized in {"healthy", "ok", "complete", "success", "degraded", "single_source"}:
        return 0.5
    return 0.0


def _deduplicate(observations: Iterable[WindowObservation]) -> tuple[list[WindowObservation], int]:
    grouped: dict[str, list[WindowObservation]] = {}
    for observation in observations:
        key = str(observation.unique_key)
        z = _finite(observation.z)
        weight = _finite(observation.weight)
        if z is None or weight is None or weight <= 0:
            continue
        grouped.setdefault(key, []).append(
            WindowObservation(key, z, min(1.0, weight), str(observation.status))
        )
    selected: list[WindowObservation] = []
    duplicate_count = 0
    for key in sorted(grouped):
        candidates = grouped[key]
        duplicate_count += max(0, len(candidates) - 1)
        # Quality first, then canonical numeric/text values.  This makes a
        # duplicated scheduler run idempotent even if the upstream order moves.
        selected.append(sorted(candidates, key=lambda item: (-item.weight, item.z, item.status))[0])
    return selected, duplicate_count


@dataclass(frozen=True)
class Posterior:
    mean: float
    variance: float
    score: float
    prior_source: str
    measured_observations: int
    effective_weight: float
    duplicate_observations: int

    @property
    def standard_deviation(self) -> float:
        return sqrt(self.variance)

    @property
    def interval80(self) -> tuple[float, float]:
        margin = NORMAL_80_Z * self.standard_deviation
        return self.mean - margin, self.mean + margin


def posterior_update(prior: Prior, observations: Iterable[WindowObservation]) -> Posterior:
    clean, duplicate_count = _deduplicate(observations)
    precision = 1.0 / prior.variance
    numerator = prior.mu / prior.variance
    effective_weight = 0.0
    for observation in clean:
        precision += observation.weight
        numerator += observation.weight * observation.z
        effective_weight += observation.weight
    variance = 1.0 / precision
    mean = numerator / precision
    return Posterior(
        mean=mean,
        variance=variance,
        score=100.0 * logistic(mean),
        prior_source=prior.source,
        measured_observations=len(clean),
        effective_weight=effective_weight,
        duplicate_observations=duplicate_count,
    )


def posterior_difference_probability(current: Posterior, previous: Posterior) -> float:
    variance = max(EPSILON, current.variance + previous.variance)
    z = (current.mean - previous.mean) / sqrt(variance)
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def direction(current: Posterior | None, previous: Posterior | None) -> str:
    if current is None or current.measured_observations == 0:
        return "no_measured"
    if previous is None:
        return "stable"
    probability = posterior_difference_probability(current, previous)
    if probability >= 0.80:
        return "improving"
    if probability <= 0.20:
        return "weakening"
    return "stable"


@dataclass(frozen=True)
class FactorPosterior:
    factor: str
    posterior: Posterior
    indicator_count: int
    measured_indicator_count: int
    direction: str


def factor_posterior(
    factor: str,
    indicator_posteriors: Mapping[str, Posterior],
    previous_indicator_posteriors: Mapping[str, Posterior] | None = None,
) -> FactorPosterior:
    if factor not in FACTOR_INDICATORS:
        raise ValueError(f"unknown factor: {factor}")
    names = FACTOR_INDICATORS[factor]
    missing = [name for name in names if name not in indicator_posteriors]
    if missing:
        raise ValueError(f"missing indicator posteriors for {factor}: {','.join(missing)}")
    values = [indicator_posteriors[name] for name in names]
    mean = sum(item.mean for item in values) / len(values)
    variance = sum(item.variance for item in values) / (len(values) * len(values))
    measured = sum(1 for item in values if item.measured_observations > 0)
    prior_source = "cohort" if all(item.prior_source == "cohort" for item in values) else "fallback"
    aggregate = Posterior(
        mean=mean,
        variance=variance,
        score=100.0 * logistic(mean),
        prior_source=prior_source,
        measured_observations=measured,
        effective_weight=sum(item.effective_weight for item in values),
        duplicate_observations=sum(item.duplicate_observations for item in values),
    )
    previous = None
    if previous_indicator_posteriors is not None and all(name in previous_indicator_posteriors for name in names):
        previous_values = [previous_indicator_posteriors[name] for name in names]
        previous = Posterior(
            mean=sum(item.mean for item in previous_values) / len(previous_values),
            variance=sum(item.variance for item in previous_values) / (len(previous_values) * len(previous_values)),
            score=0.0,
            prior_source="cohort",
            measured_observations=sum(1 for item in previous_values if item.measured_observations > 0),
            effective_weight=0.0,
            duplicate_observations=0,
        )
    return FactorPosterior(
        factor=factor,
        posterior=aggregate,
        indicator_count=len(names),
        measured_indicator_count=measured,
        direction=direction(aggregate, previous),
    )


def total_evidence_score(factors: Mapping[str, FactorPosterior]) -> Posterior:
    """Combine the five fixed-weight factor posteriors without reallocation."""

    missing = sorted(set(FACTOR_WEIGHTS) - set(factors))
    if missing:
        raise ValueError(f"missing factors: {','.join(missing)}")
    mean = sum(FACTOR_WEIGHTS[name] * factors[name].posterior.mean for name in FACTOR_WEIGHTS)
    variance = sum((FACTOR_WEIGHTS[name] ** 2) * factors[name].posterior.variance for name in FACTOR_WEIGHTS)
    return Posterior(
        mean=mean,
        variance=variance,
        score=100.0 * logistic(mean),
        prior_source="cohort" if all(factors[name].posterior.prior_source == "cohort" for name in FACTOR_WEIGHTS) else "fallback",
        measured_observations=sum(factors[name].posterior.measured_observations for name in FACTOR_WEIGHTS),
        effective_weight=sum(FACTOR_WEIGHTS[name] * factors[name].posterior.effective_weight for name in FACTOR_WEIGHTS),
        duplicate_observations=sum(factors[name].posterior.duplicate_observations for name in FACTOR_WEIGHTS),
    )


def independent_confidence(
    field_coverage: float,
    freshness: float,
    real_history: float,
    cross_source_consistency: float,
) -> float:
    """Return the separate 0-100 confidence value, never mixed into score."""

    values = (field_coverage, freshness, real_history, cross_source_consistency)
    if any(_finite(value) is None for value in values):
        raise ValueError("confidence inputs must be finite")
    weighted = (
        0.35 * float(field_coverage)
        + 0.25 * float(freshness)
        + 0.20 * float(real_history)
        + 0.20 * float(cross_source_consistency)
    )
    return min(100.0, max(0.0, weighted))


def calibration_status(result_count: int) -> str:
    return "sample_insufficient" if int(result_count) < 30 else "reportable_descriptive_only"


def calibration_snapshot(result_count: int, *, retained: int = 0, liquidity_guardrail_passed: int = 0, risk_found: int = 0) -> dict[str, Any]:
    """Build the non-predictive time-out calibration summary."""

    count = int(result_count)
    return {
        "resultCount": count,
        "status": calibration_status(count),
        "retainedCount": int(retained),
        "liquidityGuardrailPassedCount": int(liquidity_guardrail_passed),
        "hardRiskFoundCount": int(risk_found),
        "usesReturnAsLabel": False,
        "autoTuning": False,
    }


def canonical_hash(payload: Any) -> str:
    """Hash stable JSON for input lineage and snapshot reproducibility."""

    import json

    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256(encoded).hexdigest()
