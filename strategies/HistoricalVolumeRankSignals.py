"""
Historical volume-rank signal strategy for Freqtrade.

Reads:
    user_data/signals/volume_rank_signals.csv

CSV columns:
    signal_date, hold_until, pair, side, rank, avg_quote_volume

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
                self._signals_cache = signals
        return self._signals_cache

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

