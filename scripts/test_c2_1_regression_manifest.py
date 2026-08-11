#!/usr/bin/env python3

import hashlib
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def main():
    manifest = json.loads((ROOT / "docs" / "C2.1_RULE_REGRESSION_MANIFEST.json").read_text(encoding="utf-8-sig"))
    loaded = {}
    for item in manifest["realEvidenceFiles"]:
        path = ROOT / item["path"]
        assert path.exists(), item["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == item["sha256"], item["path"]
        rows = jsonl(path)
        assert len(rows) == item["physicalRows"], item["path"]
        loaded[item["path"]] = rows

    expected = manifest["observedAssertions"]
    samples = loaded["reports/c2.1-strong-path-input-probe/sample-selection.jsonl"]
    assert len(samples) == expected["fixedRealProjects"]
    assert Counter(row["effectiveAgeBand"] for row in samples) == Counter(expected["ageBandCounts"])
    assert Counter(row["networkId"] for row in samples) == Counter(expected["networkCounts"])

    quotes = loaded["reports/c2.1-strong-path-input-probe/quote-observations.jsonl"]
    assert Counter(row["state"] for row in quotes) == Counter({key: expected["standardSellQuote"][key] for key in ("success", "no_data", "unsupported")})
    assert sum(row["state"] == "success" and row.get("quoteLossPct") is not None and row["quoteLossPct"] <= 10 for row in quotes) == expected["standardSellQuote"]["successAndLossPctLte10"]
    assert sum(row["state"] == "success" and row.get("quoteLossPct") is not None and row["quoteLossPct"] >= 20 for row in quotes) == expected["standardSellQuote"]["successAndLossPctGte20"]

    products = loaded["reports/c2.1-strong-path-input-probe/product-inputs.jsonl"]
    verified_usage = sum(row.get("state") == "success" and row.get("identityState") == "verified" for row in products)
    assert verified_usage == expected["verifiedProductUsageSeries"]

    pools = loaded["reports/c2.1-path4-full-pool-supply-probe/observable-pools.jsonl"]
    assert sum(row.get("geckoEnumerationState") == "success" for row in pools) == expected["indexedPoolCoverageCompleteProjects"]

    supplies = loaded["reports/c2.1-path4-full-pool-supply-probe/supply-history.jsonl"]
    success_projects = {(row["networkId"], row["tokenAddress"]) for row in supplies if row["state"] == "success"}
    stable_projects = {(row["networkId"], row["tokenAddress"]) for row in supplies if row["state"] == "success" and row.get("supplyStabilityCategory") != "unit_scale_changed"}
    assert len(success_projects) == expected["historicalSupplySuccessProjects"]
    assert len(stable_projects) == expected["historicalSupplyUnitScaleStableProjects"]

    path4 = loaded["reports/c2.1-path4-full-pool-supply-probe/path4-inputs.jsonl"]
    assert sum(row.get("coverageState") == "complete_for_observable_set" for row in path4) == expected["strictAllDiscoveredPoolCoverageCompleteProjects"]
    assert sum(row.get("riskAdjustedSurplus") is not None and row["riskAdjustedSurplus"] > 0 for row in path4) == expected["path4CandidateUnderDraftP60AndPositiveRiskAdjustedSurplus"]
    print("C2.1 frozen regression manifest tests passed")


if __name__ == "__main__":
    main()
