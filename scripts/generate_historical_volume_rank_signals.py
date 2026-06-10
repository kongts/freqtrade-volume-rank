"""
Generate historical volume-rank long/short signals for Freqtrade backtesting.

No future leak:
    On each rebalance date, rank pairs by the previous 60 days average
    quote volume, then create signals for the next holding window.

Default:
    - Rebalance every 60 days
    - Lookback 60 days
    - Long rank 1-10
    - Short rank 41-50
    - Hold 20 days

Run on server:
    cd ~/freqtrade
    python3 user_data/scripts/generate_historical_volume_rank_signals.py
"""

from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd


DEFAULT_CONFIG = Path("user_data/config.json")
DEFAULT_DATA_DIR = Path("user_data/data/binance")
DEFAULT_OUTPUT = Path("user_data/signals/volume_rank_signals.csv")


def parse_date(value: str) -> datetime:
    return datetime.strptime(value, "%Y%m%d").replace(tzinfo=timezone.utc)


def pair_to_safe_name(pair: str) -> str:
    return pair.replace("/", "_").replace(":", "_")


def candidate_data_files(data_dir: Path, pair: str, timeframe: str) -> list[Path]:
    safe = pair_to_safe_name(pair)
    return [
        data_dir / "futures" / f"{safe}-{timeframe}-futures.feather",
        data_dir / "futures" / f"{safe}-{timeframe}.json",
        data_dir / f"{safe}-{timeframe}.json",
        data_dir / "futures" / f"{safe}-{timeframe}.feather",
        data_dir / f"{safe}-{timeframe}.feather",
    ]


def load_json_ohlcv(path: Path) -> list[dict]:
    raw = json.loads(path.read_text())
    rows = []
    for item in raw:
        # Freqtrade json ohlcv format is usually:
        # [timestamp_ms, open, high, low, close, volume]
        if len(item) < 6:
            continue
        timestamp_ms = int(item[0])
        close = float(item[4])
        base_volume = float(item[5])
        rows.append(
            {
                "timestamp": datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
                "close": close,
                "quote_volume": close * base_volume,
            }
        )
    return rows


def load_feather_ohlcv(path: Path) -> list[dict]:
    df = pd.read_feather(path)
    rows = []
    for item in df.to_dict("records"):
        timestamp = item.get("date") or item.get("timestamp")
        if timestamp is None:
            continue
        timestamp = pd.Timestamp(timestamp)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")

        close = float(item["close"])
        base_volume = float(item["volume"])
        rows.append(
            {
                "timestamp": timestamp.to_pydatetime(),
                "close": close,
                "quote_volume": close * base_volume,
            }
        )
    return rows


def load_pair_data(data_dir: Path, pair: str, timeframe: str) -> list[dict]:
    for path in candidate_data_files(data_dir, pair, timeframe):
        if path.exists() and path.suffix == ".json":
            return load_json_ohlcv(path)
        if path.exists() and path.suffix == ".feather":
            return load_feather_ohlcv(path)
    return []


def load_pairs(config_path: Path) -> list[str]:
    config = json.loads(config_path.read_text())
    return config.get("exchange", {}).get("pair_whitelist", [])


def safe_name_to_pair(safe_name: str) -> str | None:
    # BTC_USDT_USDT -> BTC/USDT:USDT
    parts = safe_name.split("_")
    if len(parts) < 3:
        return None
    base = "_".join(parts[:-2])
    quote = parts[-2]
    settle = parts[-1]
    return f"{base}/{quote}:{settle}"


def discover_pairs_from_data(data_dir: Path, timeframe: str) -> list[str]:
    futures_dir = data_dir / "futures"
    if not futures_dir.exists():
        return []
    suffix = f"-{timeframe}-futures.feather"
    pairs = []
    for path in sorted(futures_dir.glob(f"*{suffix}")):
        safe_name = path.name[: -len(suffix)]
        pair = safe_name_to_pair(safe_name)
        if pair:
            pairs.append(pair)
    return pairs


def average_quote_volume(rows: list[dict], start: datetime, end: datetime) -> tuple[float | None, int]:
    values = [row["quote_volume"] for row in rows if start <= row["timestamp"] < end]
    if not values:
        return None, 0
    return sum(values) / len(values), len(values)


def first_timestamp(data_by_pair: dict[str, list[dict]]) -> datetime:
    return min(rows[0]["timestamp"] for rows in data_by_pair.values() if rows)


def last_timestamp(data_by_pair: dict[str, list[dict]]) -> datetime:
    return max(rows[-1]["timestamp"] for rows in data_by_pair.values() if rows)


def generate_signals(args: argparse.Namespace) -> list[dict]:
    pairs = load_pairs(Path(args.config))
    discovered_pairs = discover_pairs_from_data(Path(args.data_dir), args.timeframe)
    if len(pairs) < args.top_n and discovered_pairs:
        pairs = discovered_pairs
    data_by_pair = {
        pair: load_pair_data(Path(args.data_dir), pair, args.timeframe)
        for pair in pairs
    }
    data_by_pair = {pair: rows for pair, rows in data_by_pair.items() if rows}
    if not data_by_pair:
        raise RuntimeError("No local OHLCV data found. Run freqtrade download-data first.")

    start = parse_date(args.timerange_start) if args.timerange_start else first_timestamp(data_by_pair)
    end = parse_date(args.timerange_end) if args.timerange_end else last_timestamp(data_by_pair)

    signal_time = start + timedelta(days=args.lookback_days)
    last_signal_time = end - timedelta(days=args.holding_days)
    signals = []

    while signal_time <= last_signal_time:
        lookback_start = signal_time - timedelta(days=args.lookback_days)
        ranked = []
        for pair, rows in data_by_pair.items():
            avg_volume, bar_count = average_quote_volume(rows, lookback_start, signal_time)
            if avg_volume is None:
                continue
            if bar_count < args.min_lookback_bars:
                continue
            ranked.append(
                {
                    "pair": pair,
                    "avg_quote_volume": avg_volume,
                    "bar_count": bar_count,
                }
            )
        ranked.sort(key=lambda row: row["avg_quote_volume"], reverse=True)
        top = ranked[: args.top_n]
        if len(top) >= args.short_end_rank:
            hold_until = signal_time + timedelta(days=args.holding_days)
            for rank, row in enumerate(top, start=1):
                if 1 <= rank <= args.long_n:
                    side = "long"
                elif args.short_start_rank <= rank <= args.short_end_rank:
                    side = "short"
                else:
                    continue
                signals.append(
                    {
                        "signal_date": signal_time.date().isoformat(),
                        "hold_until": hold_until.date().isoformat(),
                        "pair": row["pair"],
                        "side": side,
                        "rank": rank,
                        "avg_quote_volume": row["avg_quote_volume"],
                        "lookback_bar_count": row["bar_count"],
                    }
                )
        signal_time += timedelta(days=args.rebalance_days)

    return signals


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "signal_date",
        "hold_until",
        "pair",
        "side",
        "rank",
        "avg_quote_volume",
        "lookback_bar_count",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate historical volume-rank signals.")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--timeframe", default="2h")
    parser.add_argument("--timerange-start", default="")
    parser.add_argument("--timerange-end", default="")
    parser.add_argument("--lookback-days", type=int, default=60)
    parser.add_argument("--rebalance-days", type=int, default=60)
    parser.add_argument("--holding-days", type=int, default=20)
    parser.add_argument("--top-n", type=int, default=50)
    parser.add_argument("--long-n", type=int, default=10)
    parser.add_argument("--short-start-rank", type=int, default=41)
    parser.add_argument("--short-end-rank", type=int, default=50)
    parser.add_argument("--min-lookback-bars", type=int, default=360)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    signals = generate_signals(args)
    write_csv(Path(args.output), signals)
    print(f"Wrote {args.output}")
    print(f"signals={len(signals)}")
    print(f"trade_dates={len(set(row['signal_date'] for row in signals))}")


if __name__ == "__main__":
    main()
