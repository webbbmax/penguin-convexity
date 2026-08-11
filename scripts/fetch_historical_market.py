#!/usr/bin/env python3
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CASES_PATH = PROJECT_ROOT / "fixtures" / "real-historical-cases-v1.json"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "fixtures" / "real-historical-market-v1.json"
BINANCE_KLINES_URL = "https://api.binance.com/api/v3/klines"


def utc_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def day_to_millis(value):
    return int(datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc).timestamp() * 1000)


def fetch_daily_klines(symbol, start_day, end_day):
    query = urllib.parse.urlencode(
        {
            "symbol": symbol,
            "interval": "1d",
            "startTime": day_to_millis(start_day),
            "endTime": day_to_millis(end_day),
            "limit": 1000,
        }
    )
    request = urllib.request.Request(
        f"{BINANCE_KLINES_URL}?{query}",
        headers={"User-Agent": "PenguinResearchConvexity/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return [
        {
            "date": datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).date().isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in payload
    ]


def closest_close(rows, target_day):
    if not rows:
        return None
    target = date.fromisoformat(target_day)
    row = min(rows, key=lambda item: abs((date.fromisoformat(item["date"]) - target).days))
    return {"date": row["date"], "close": row["close"]}


def pct_change(start, end):
    if not start or not end or start["close"] == 0:
        return None
    return round((end["close"] / start["close"] - 1) * 100, 2)


def summarize_rows(rows, event_day):
    event = date.fromisoformat(event_day)
    points = {
        "pre30": closest_close(rows, (event - timedelta(days=30)).isoformat()),
        "event": closest_close(rows, event.isoformat()),
        "post7": closest_close(rows, (event + timedelta(days=7)).isoformat()),
        "post30": closest_close(rows, (event + timedelta(days=30)).isoformat()),
        "post90": closest_close(rows, (event + timedelta(days=90)).isoformat()),
    }
    event_close = points["event"]
    return {
        "status": "available",
        "points": points,
        "changesPct": {
            "pre30ToEvent": pct_change(points["pre30"], event_close),
            "eventToPost7": pct_change(event_close, points["post7"]),
            "eventToPost30": pct_change(event_close, points["post30"]),
            "eventToPost90": pct_change(event_close, points["post90"]),
        },
        "window": {
            "start": rows[0]["date"],
            "end": rows[-1]["date"],
            "maximumClose": max(item["close"] for item in rows),
            "minimumClose": min(item["close"] for item in rows),
            "observationCount": len(rows),
        },
    }


def unavailable(reason, detail):
    return {"status": "unavailable", "reason": reason, "detail": detail}


def build_market_snapshot(cases_path=DEFAULT_CASES_PATH):
    fixtures = json.loads(Path(cases_path).read_text(encoding="utf-8"))
    results = {}
    for case in fixtures["cases"]:
        market = case.get("market") or {}
        symbol = market.get("symbol")
        if not symbol:
            results[case["caseId"]] = unavailable(
                "no_supported_symbol",
                "案例没有可用的 Binance Spot 历史交易对，行情保持缺失。",
            )
            continue

        event = date.fromisoformat(case["eventAt"])
        start_day = event - timedelta(days=35)
        end_day = event + timedelta(days=95)
        try:
            rows = fetch_daily_klines(symbol, start_day, end_day)
            if not rows:
                results[case["caseId"]] = unavailable(
                    "pair_not_listed_at_event",
                    f"{symbol} 在事件窗口没有返回交易记录，可能当时尚未上市或历史交易对已下线。",
                )
            else:
                results[case["caseId"]] = {
                    "provider": market["provider"],
                    "symbol": symbol,
                    "eventAt": case["eventAt"],
                    **summarize_rows(rows, case["eventAt"]),
                }
        except urllib.error.HTTPError as error:
            reason = "unsupported_or_delisted" if error.code in (400, 404) else "upstream_http_error"
            results[case["caseId"]] = unavailable(
                reason,
                f"{symbol} 行情请求失败（HTTP {error.code}），不使用估算值替代。",
            )
        except (urllib.error.URLError, TimeoutError, ValueError) as error:
            results[case["caseId"]] = unavailable(
                "temporary_source_failure",
                f"{symbol} 行情暂时无法获取：{type(error).__name__}。",
            )
        time.sleep(0.08)

    return {
        "version": "convexity-real-market-v1.0.0",
        "generatedAt": utc_now(),
        "source": {
            "name": "Binance Spot public market data",
            "endpoint": BINANCE_KLINES_URL,
            "purpose": "只用于观察事件前后价格反应，不作为事件事实或因果证据。",
        },
        "results": results,
    }


def write_snapshot(snapshot, output_path=DEFAULT_OUTPUT_PATH):
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(f"{output_path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(output_path)
    return output_path


def main():
    parser = argparse.ArgumentParser(description="抓取真实案例事件窗口的公开历史行情")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    args = parser.parse_args()
    snapshot = build_market_snapshot(args.cases)
    write_snapshot(snapshot, args.output)
    available = sum(1 for item in snapshot["results"].values() if item["status"] == "available")
    print(
        json.dumps(
            {
                "status": "success",
                "cases": len(snapshot["results"]),
                "marketAvailable": available,
                "marketUnavailable": len(snapshot["results"]) - available,
                "output": str(args.output.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
