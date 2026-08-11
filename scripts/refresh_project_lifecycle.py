#!/usr/bin/env python3
import argparse
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contract_tradeability import user_environment
from init_db import DEFAULT_DB_PATH


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "fixtures" / "candidate-refresh-sources-v1.json"
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "project-lifecycle-cache-v1.json"
USER_AGENT = "Penguin-Convexity/1.0"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def load_json(path, fallback):
    path = Path(path)
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def project_coin_ids(connection, config_path=DEFAULT_CONFIG_PATH):
    config = load_json(config_path, {"projects": []})
    case_to_project = {
        row["case_id"]: row["project_id"]
        for row in connection.execute(
            "SELECT case_id, project_id FROM candidate_cases"
        )
    }
    result = {}
    for item in config.get("projects", []):
        if item.get("provider") != "coingecko" or not item.get("coinId"):
            continue
        project_id = case_to_project.get(item.get("caseId"))
        if project_id:
            result[project_id] = item["coinId"]

    rows = connection.execute(
        """
        SELECT matched_project_id, promoted_project_id, coingecko_id, reviewed_at
        FROM discovery_identity_reviews
        WHERE coingecko_id <> ''
          AND (matched_project_id IS NOT NULL OR promoted_project_id IS NOT NULL)
        ORDER BY reviewed_at
        """
    )
    for row in rows:
        project_id = row["promoted_project_id"] or row["matched_project_id"]
        if project_id:
            result[project_id] = row["coingecko_id"]
    return result


def request_market_history(coin_id, api_key, timeout=20):
    query = urllib.parse.urlencode(
        {"vs_currency": "usd", "days": "365", "interval": "daily"}
    )
    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart?{query}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "x-cg-demo-api-key": api_key,
        },
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.load(response)
            prices = payload.get("prices") or []
            if not prices:
                raise RuntimeError("CoinGecko 没有返回历史价格")
            first_at = datetime.fromtimestamp(
                prices[0][0] / 1000, tz=timezone.utc
            )
            window_start = datetime.now(timezone.utc) - timedelta(days=365)
            return {
                "status": "available",
                "earliestMarketDate": first_at.date().isoformat(),
                "historyWindowDays": 365,
                "lowerBoundOnly": first_at <= window_start + timedelta(days=3),
                "sourceName": "CoinGecko",
                "sourceUrl": f"https://www.coingecko.com/en/coins/{coin_id}",
                "fetchedAt": utc_now(),
                "error": "",
            }
        except urllib.error.HTTPError as error:
            if error.code != 429 or attempt == 2:
                raise
            time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError):
            if attempt == 2:
                raise
            time.sleep(0.8 * (attempt + 1))


def refresh_lifecycle_cache(
    connection,
    cache_path=DEFAULT_CACHE_PATH,
    config_path=DEFAULT_CONFIG_PATH,
    timeout=20,
    max_age_days=30,
):
    cache = load_json(
        cache_path,
        {"version": "C1.2-05", "generatedAt": "", "projects": {}},
    )
    projects = cache.setdefault("projects", {})
    coin_ids = project_coin_ids(connection, config_path)
    api_key = user_environment("COINGECKO_DEMO_API_KEY")
    now = datetime.now(timezone.utc)
    updated = 0
    failed = 0

    if not api_key:
        return {
            "status": "skipped",
            "updated": 0,
            "failed": 0,
            "reason": "CoinGecko Demo API 未配置",
        }

    for project_id, coin_id in sorted(coin_ids.items()):
        existing = projects.get(project_id) or {}
        fetched_at = existing.get("fetchedAt")
        if fetched_at:
            parsed = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
            if now - parsed < timedelta(days=max_age_days):
                continue
        try:
            projects[project_id] = {
                "projectId": project_id,
                "coinGeckoId": coin_id,
                **request_market_history(coin_id, api_key, timeout),
            }
            updated += 1
        except Exception as error:
            failed += 1
            if not existing:
                projects[project_id] = {
                    "projectId": project_id,
                    "coinGeckoId": coin_id,
                    "status": "failed",
                    "earliestMarketDate": "",
                    "historyWindowDays": 365,
                    "lowerBoundOnly": False,
                    "sourceName": "CoinGecko",
                    "sourceUrl": f"https://www.coingecko.com/en/coins/{coin_id}",
                    "fetchedAt": utc_now(),
                    "error": f"{type(error).__name__}: {error}",
                }
        time.sleep(0.2)

    cache["version"] = "C1.2-05"
    cache["generatedAt"] = utc_now()
    cache["projectCount"] = len(projects)
    cache_path = Path(cache_path)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = cache_path.with_suffix(f"{cache_path.suffix}.tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(cache_path)
    return {
        "status": "partial_success" if failed else "success",
        "updated": updated,
        "failed": failed,
        "projectCount": len(projects),
    }


def main():
    parser = argparse.ArgumentParser(description="刷新凸性项目生命周期市场起点")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--cache", type=Path, default=DEFAULT_CACHE_PATH)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    try:
        result = refresh_lifecycle_cache(
            connection,
            args.cache,
            args.config,
            args.timeout,
            max_age_days=0 if args.force else 30,
        )
    finally:
        connection.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
