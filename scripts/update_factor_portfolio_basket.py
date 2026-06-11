"""
Update the live factor portfolio basket for Freqtrade.

Model translated from the research notebook/scripts:
    - Universe: current Binance USDT-M perpetual futures quote-volume Top100.
    - Long basket: drawdown_60d Top10, where less drawdown means closer to 60d high.
    - Short basket: low log_avg_qv_60d pool, weakest taker_buy_ratio_20d,
      excluding price-repaired-high, quote-volume jump, oversold rebound,
      and extreme pre-day funding.
    - Rebalance/signal refresh: every 30 days.
    - Entry trigger: virtual 50/50 portfolio return from signal baseline <= -1%.
    - Exit trigger: virtual 50/50 portfolio return from entry baseline >= +4%.

Run:
    cd ~/freqtrade
    python3 user_data/scripts/update_factor_portfolio_basket.py
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean


BASE_URL = "https://fapi.binance.com"
CONFIG_PATH = Path("user_data/config-factor-testnet.json")
BASKET_PATH = Path("user_data/current_factor_portfolio_basket.json")

INTERVAL = "2h"
CANDLES_LIMIT = 720
UNIVERSE_N = 100
LOW_QV_POOL_N = 60
LONG_N = 10
SHORT_N = 10
SIGNAL_REFRESH_DAYS = 30
ENTRY_TRIGGER = -0.01
TAKE_PROFIT = 0.07
EXIT_HOLD_HOURS = 6
FUNDING_DAILY_ABS_LIMIT = 0.001

FILTER_CFG = {
    "range_high": 0.70,
    "qv_jump": 0.50,
    "oversold_dd": -0.50,
    "oversold_range_rebound": 0.40,
}

NON_CRYPTO_BASES = {"XAU", "XAG", "XAUT", "PAXG"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def get_json(path: str, params: dict | None = None):
    url = BASE_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "factor-portfolio-basket/1.0"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def to_pair(symbol: str) -> str | None:
    if not symbol.endswith("USDT"):
        return None
    return f"{symbol[:-4]}/USDT:USDT"


def symbol_from_pair(pair: str) -> str:
    return pair.replace("/USDT:USDT", "USDT")


def fetch_top_symbols() -> list[dict]:
    exchange_info = get_json("/fapi/v1/exchangeInfo")
    tickers = get_json("/fapi/v1/ticker/24hr")
    info_by_symbol = {item["symbol"]: item for item in exchange_info["symbols"]}
    rows = []

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
        pair = to_pair(symbol)
        if not pair:
            continue
        rows.append(
            {
                "symbol": symbol,
                "pair": pair,
                "base_asset": info.get("baseAsset"),
                "quote_volume_24h": float(ticker.get("quoteVolume") or 0.0),
                "last_price": float(ticker.get("lastPrice") or 0.0),
            }
        )

    rows.sort(key=lambda row: row["quote_volume_24h"], reverse=True)
    return rows[:UNIVERSE_N]


def fetch_klines(symbol: str) -> list[dict]:
    raw = get_json("/fapi/v1/klines", {"symbol": symbol, "interval": INTERVAL, "limit": CANDLES_LIMIT})
    rows = []
    for item in raw:
        high = float(item[2])
        low = float(item[3])
        close = float(item[4])
        quote_volume = float(item[7])
        taker_buy_quote = float(item[10])
        rows.append(
            {
                "open_time": int(item[0]),
                "high": high,
                "low": low,
                "close": close,
                "quote_volume": quote_volume,
                "taker_buy_ratio": taker_buy_quote / quote_volume if quote_volume > 0 else math.nan,
            }
        )
    return rows


def fetch_pre_day_funding(symbol: str) -> float:
    end_ms = int(utc_now().timestamp() * 1000)
    start_ms = int((utc_now() - timedelta(days=1)).timestamp() * 1000)
    raw = get_json("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start_ms, "endTime": end_ms, "limit": 10})
    return sum(float(x.get("fundingRate") or 0.0) for x in raw)


def window(rows: list[dict], days: int) -> list[dict]:
    return rows[-days * 12 :]


def avg(values: list[float]) -> float:
    values = [x for x in values if not math.isnan(x)]
    return mean(values) if values else math.nan


def calc_features(top_symbols: list[dict]) -> list[dict]:
    features = []
    for idx, row in enumerate(top_symbols, start=1):
        symbol = row["symbol"]
        try:
            klines = fetch_klines(symbol)
            time.sleep(0.03)
            if len(klines) < CANDLES_LIMIT:
                continue

            seg20 = window(klines, 20)
            seg60 = window(klines, 60)
            prev20 = klines[-40 * 12 : -20 * 12]
            last_close = seg60[-1]["close"]
            high20 = max(x["high"] for x in seg20)
            low20 = min(x["low"] for x in seg20)
            high60_close = max(x["close"] for x in seg60)
            avg_qv20 = avg([x["quote_volume"] for x in seg20])
            prev_qv20 = avg([x["quote_volume"] for x in prev20])
            avg_qv60 = avg([x["quote_volume"] for x in seg60])

            qv_change20 = avg_qv20 / prev_qv20 - 1 if prev_qv20 and prev_qv20 > 0 else math.nan
            range_pos20 = (last_close - low20) / (high20 - low20) if high20 > low20 else math.nan
            drawdown60 = last_close / high60_close - 1 if high60_close > 0 else math.nan

            features.append(
                {
                    **row,
                    "volume_rank_24h": idx,
                    "last_close": last_close,
                    "avg_qv_60d": avg_qv60,
                    "log_avg_qv_60d": math.log1p(avg_qv60),
                    "drawdown_60d": drawdown60,
                    "range_pos_20d": range_pos20,
                    "qv_change_20d": qv_change20,
                    "taker_buy_ratio_20d": avg([x["taker_buy_ratio"] for x in seg20]),
                    "pre_daily_funding": fetch_pre_day_funding(symbol),
                }
            )
            time.sleep(0.03)
        except Exception as exc:
            print(f"skip {symbol}: {exc}")
    return features


def build_basket(features: list[dict]) -> tuple[list[dict], list[dict]]:
    usable = [x for x in features if abs(x.get("pre_daily_funding", 0.0)) <= FUNDING_DAILY_ABS_LIMIT]
    long_rows = sorted(usable, key=lambda x: x["drawdown_60d"], reverse=True)[:LONG_N]
    long_symbols = {x["symbol"] for x in long_rows}

    low_pool = sorted(features, key=lambda x: x["log_avg_qv_60d"])[:LOW_QV_POOL_N]
    short_candidates = []
    for row in low_pool:
        if row["symbol"] in long_symbols:
            continue
        price_repaired = row["range_pos_20d"] >= FILTER_CFG["range_high"]
        qv_jump = row["qv_change_20d"] >= FILTER_CFG["qv_jump"]
        oversold_rebound = (
            row["drawdown_60d"] <= FILTER_CFG["oversold_dd"]
            and row["range_pos_20d"] >= FILTER_CFG["oversold_range_rebound"]
        )
        funding_extreme = abs(row.get("pre_daily_funding", 0.0)) > FUNDING_DAILY_ABS_LIMIT
        if not (price_repaired or qv_jump or oversold_rebound or funding_extreme):
            short_candidates.append(row)
    short_rows = sorted(short_candidates, key=lambda x: x["taker_buy_ratio_20d"])[:SHORT_N]
    return long_rows, short_rows


def side_return(rows: list[dict], ref_prices: dict[str, float], side: str) -> float:
    returns = []
    for row in rows:
        ref = ref_prices.get(row["symbol"])
        px = row.get("last_close")
        if not ref or not px:
            continue
        returns.append(px / ref - 1 if side == "long" else ref / px - 1)
    return mean(returns) if returns else 0.0


def portfolio_return(long_rows: list[dict], short_rows: list[dict], ref_prices: dict[str, float]) -> float:
    return 0.5 * side_return(long_rows, ref_prices, "long") + 0.5 * side_return(short_rows, ref_prices, "short")


def load_state() -> dict:
    if BASKET_PATH.exists():
        return json.loads(BASKET_PATH.read_text(encoding="utf-8"))
    return {}


def write_config_pairs(pairs: list[str]) -> None:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    config.setdefault("exchange", {})
    config["exchange"]["pair_whitelist"] = pairs
    config["pairlists"] = [{"method": "StaticPairList"}]
    config["max_open_trades"] = max(config.get("max_open_trades", 0), len(pairs))
    CONFIG_PATH.write_text(json.dumps(config, indent=4, ensure_ascii=False), encoding="utf-8")


def main() -> None:
    now = utc_now()
    state = load_state()
    signal_time = parse_dt(state.get("signal_time"))
    needs_new_signal = signal_time is None or now >= signal_time + timedelta(days=SIGNAL_REFRESH_DAYS)

    top_symbols = fetch_top_symbols()
    features = calc_features(top_symbols)
    long_rows, short_rows = build_basket(features)

    signal_prices = state.get("signal_reference_prices", {})
    entry_prices = state.get("entry_reference_prices", {})
    trade_sequence = int(state.get("trade_sequence", 0))
    entries_enabled = bool(state.get("entries_enabled", False))
    exit_enabled = False
    exit_hold_until = parse_dt(state.get("exit_hold_until"))

    if needs_new_signal:
        signal_time = now
        signal_prices = {x["symbol"]: x["last_close"] for x in long_rows + short_rows}
        entry_prices = {}
        trade_sequence = 0
        entries_enabled = False
        exit_hold_until = None

    virtual_return = portfolio_return(long_rows, short_rows, signal_prices)

    if exit_hold_until and now < exit_hold_until:
        exit_enabled = True
    elif entries_enabled and entry_prices:
        live_trade_return = portfolio_return(long_rows, short_rows, entry_prices)
        if live_trade_return >= TAKE_PROFIT:
            exit_enabled = True
            entries_enabled = False
            entry_prices = {}
            exit_hold_until = now + timedelta(hours=EXIT_HOLD_HOURS)
    else:
        live_trade_return = math.nan

    if not entries_enabled and not exit_enabled and virtual_return <= ENTRY_TRIGGER:
        entries_enabled = True
        trade_sequence += 1
        entry_prices = {x["symbol"]: x["last_close"] for x in long_rows + short_rows}
        live_trade_return = 0.0

    pairs = [x["pair"] for x in long_rows + short_rows]
    basket = {
        "updated_at": now.isoformat(),
        "signal_time": signal_time.isoformat() if signal_time else None,
        "signal_refresh_days": SIGNAL_REFRESH_DAYS,
        "trade_sequence": trade_sequence,
        "entries_enabled": entries_enabled,
        "exit_enabled": exit_enabled,
        "entry_trigger": ENTRY_TRIGGER,
        "take_profit": TAKE_PROFIT,
        "virtual_return_from_signal": virtual_return,
        "virtual_return_from_entry": live_trade_return,
        "signal_reference_prices": signal_prices,
        "entry_reference_prices": entry_prices,
        "long_pairs": [x["pair"] for x in long_rows],
        "short_pairs": [x["pair"] for x in short_rows],
        "long_rows": long_rows,
        "short_rows": short_rows,
        "top100_feature_rows": features,
    }
    BASKET_PATH.write_text(json.dumps(basket, indent=4, ensure_ascii=False), encoding="utf-8")
    write_config_pairs(pairs)

    print(f"updated {BASKET_PATH}")
    print(f"entries_enabled={entries_enabled} exit_enabled={exit_enabled}")
    print(f"virtual_return_from_signal={virtual_return:.4%}")
    if not math.isnan(live_trade_return):
        print(f"virtual_return_from_entry={live_trade_return:.4%}")
    print("LONG", ", ".join(basket["long_pairs"]))
    print("SHORT", ", ".join(basket["short_pairs"]))


if __name__ == "__main__":
    main()
