"""
Historical volume-rank signal strategy for Freqtrade.

Reads:
    user_data/signals/volume_rank_signals.csv

CSV columns:
    signal_date, hold_until, pair, side, rank, avg_quote_volume, stake_weight

The signal CSV must be generated without future leak by
generate_historical_volume_rank_signals.py.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from pandas import DataFrame

from freqtrade.strategy import IStrategy


class HistoricalVolumeRankSignals(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "2h"
    can_short = True

    startup_candle_count = 1
    process_only_new_candles = True

    minimal_roi = {"0": 100}
    stoploss = -0.99
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    signal_path = Path("user_data/signals/volume_rank_signals.csv")
    _signals_cache = None

    def _signals(self) -> pd.DataFrame:
        if self._signals_cache is None:
            if not self.signal_path.exists():
                self._signals_cache = pd.DataFrame(
                    columns=["signal_date", "hold_until", "pair", "side"]
                )
            else:
                signals = pd.read_csv(self.signal_path)
                signals["signal_date"] = pd.to_datetime(signals["signal_date"], utc=True)
                signals["hold_until"] = pd.to_datetime(signals["hold_until"], utc=True)
                if "expected_open_time" in signals.columns:
                    signals["expected_open_time"] = pd.to_datetime(
                        signals["expected_open_time"], utc=True
                    )
                if "stake_weight" not in signals.columns:
                    signals["stake_weight"] = 0.0
                self._signals_cache = signals
        return self._signals_cache

    def _entry_signal(self, pair: str, current_time, side: str | None):
        signals = self._signals()
        if signals.empty:
            return None

        timestamp = pd.Timestamp(current_time)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize("UTC")
        else:
            timestamp = timestamp.tz_convert("UTC")

        side_value = "short" if side == "short" else "long"
        pair_signals = signals[
            (signals["pair"] == pair)
            & (signals["side"] == side_value)
            & (signals["signal_date"].dt.date == timestamp.date())
        ]
        if pair_signals.empty and "expected_open_time" in signals.columns:
            pair_signals = signals[
                (signals["pair"] == pair)
                & (signals["side"] == side_value)
                & (signals["expected_open_time"].dt.date == timestamp.date())
            ]
        if pair_signals.empty:
            return None
        return pair_signals.iloc[0]

    def custom_stake_amount(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_stake: float,
        min_stake: float | None,
        max_stake: float,
        leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        signal = self._entry_signal(pair, current_time, side)
        if signal is None or not signal.get("stake_weight", 0):
            return proposed_stake

        wallet = float(self.config.get("dry_run_wallet") or 1000)
        wallet *= float(self.config.get("tradable_balance_ratio") or 1)
        stake = wallet * float(signal["stake_weight"])

        if min_stake is not None:
            stake = max(stake, float(min_stake))
        if max_stake is not None and max_stake > 0:
            stake = min(stake, float(max_stake))
        return stake

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        signals = self._signals()
        if dataframe.empty or signals.empty:
            return dataframe

        pair_signals = signals[signals["pair"] == metadata["pair"]]
        if pair_signals.empty:
            return dataframe

        for _, signal in pair_signals.iterrows():
            mask = dataframe["date"] == signal["signal_date"]
            if signal["side"] == "long":
                dataframe.loc[mask, "enter_long"] = 1
                dataframe.loc[mask, "enter_tag"] = "historical_volume_rank_long"
            elif signal["side"] == "short":
                dataframe.loc[mask, "enter_short"] = 1
                dataframe.loc[mask, "enter_tag"] = "historical_volume_rank_short"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0

        signals = self._signals()
        if dataframe.empty or signals.empty:
            return dataframe

        pair_signals = signals[signals["pair"] == metadata["pair"]]
        if pair_signals.empty:
            return dataframe

        for _, signal in pair_signals.iterrows():
            mask = dataframe["date"] >= signal["hold_until"]
            mask &= dataframe["date"] < signal["hold_until"] + pd.Timedelta(hours=2)
            dataframe.loc[mask, "exit_long"] = 1
            dataframe.loc[mask, "exit_short"] = 1

        return dataframe
