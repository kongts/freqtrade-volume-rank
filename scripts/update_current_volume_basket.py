"""
Update current Binance USDT-M futures volume basket for Freqtrade.

Logic:
    - Fetch Binance USDT-M perpetual futures 24h tickers.
    - Rank by current 24h quoteVolume.
    - Top50 universe.
    - Long: rank 1-10.
    - Short: rank 41-50.
    - Write user_data/current_volume_basket.json.
    - Update user_data/config.json pair_whitelist to long + short pairs.

Run on server:
    cd ~/freqtrade
    python3 user_data/scripts/update_current_volume_basket.py
"""

from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


TOP_N = 50
LONG_N = 10
SHORT_START_RANK = 41
SHORT_END_RANK = 50

CONFIG_PATH = Path("user_data/config.json")
BASKET_PATH = Path("user_data/current_volume_basket.json")

NON_CRYPTO_BASES = {"XAU", "XAG"}


def get_json(url: str):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "freqtrade-current-volume-basket/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def to_freqtrade_pair(symbol: str) -> str | None:
    if not symbol.endswith("USDT"):
        return None
    base = symbol[:-4]
    return f"{base}/USDT:USDT"


def main() -> None:
    exchange_info = get_json("https://fapi.binance.com/fapi/v1/exchangeInfo")
    tickers = get_json("https://fapi.binance.com/fapi/v1/ticker/24hr")

    info_by_symbol = {item["symbol"]: item for item in exchange_info["symbols"]}
    candidates = []

    for ticker in tickers:
        symbol = ticker.get("symbol")
        info = info_by_symbol.get(symbol)
        if not symbol or not info:
            continue
        if info.get("status") != "TRADING":
            continue
        if info.get("contractType") != "PERPETUAL":
            continue
        if info.get("quoteAsset") != "USDT":
            continue
        if info.get("baseAsset") in NON_CRYPTO_BASES:
            continue

        pair = to_freqtrade_pair(symbol)
        if not pair:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "pair": pair,
                "base_asset": info.get("baseAsset"),
                "quote_volume_24h": float(ticker.get("quoteVolume") or 0.0),
            }
        )

    candidates.sort(key=lambda row: row["quote_volume_24h"], reverse=True)
    top50 = candidates[:TOP_N]
    long_rows = top50[:LONG_N]
    short_rows = top50[SHORT_START_RANK - 1 : SHORT_END_RANK]

    long_pairs = [row["pair"] for row in long_rows]
    short_pairs = [row["pair"] for row in short_rows]
    whitelist = long_pairs + short_pairs

    basket = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "ranking_metric": "binance_futures_24h_quote_volume",
        "top_n": TOP_N,
        "long_rank_range": [1, LONG_N],
        "short_rank_range": [SHORT_START_RANK, SHORT_END_RANK],
        "long_pairs": long_pairs,
        "short_pairs": short_pairs,
        "top50": top50,
    }

    BASKET_PATH.write_text(json.dumps(basket, indent=4), encoding="utf-8")

    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("exchange", {})
    config["exchange"]["pair_whitelist"] = whitelist
    config["pairlists"] = [{"method": "StaticPairList"}]
    config["max_open_trades"] = max(config.get("max_open_trades", 0), len(whitelist))
    CONFIG_PATH.write_text(json.dumps(config, indent=4), encoding="utf-8")

    print(f"Wrote {BASKET_PATH}")
    print(f"Updated {CONFIG_PATH} pair_whitelist with {len(whitelist)} pairs")
    print("LONG:")
    for pair in long_pairs:
        print(f"  {pair}")
    print("SHORT:")
    for pair in short_pairs:
        print(f"  {pair}")


if __name__ == "__main__":
    main()

