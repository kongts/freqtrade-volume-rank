"""
Current Volume Rank Long/Short Strategy for Freqtrade.

External signal file:
    user_data/current_volume_basket.json

Basket logic is computed outside the strategy:
    - Long current Binance USDT-M futures volume rank 1-10.
    - Short current Binance USDT-M futures volume rank 41-50.

This strategy only executes the current basket.
Use dry-run first.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class CurrentVolumeRankLongShort(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "2h"
    can_short = True

    startup_candle_count = 1
    process_only_new_candles = True

    minimal_roi = {"0": 100}
    stoploss = -0.99
    use_custom_stoploss = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    holding_days = 20
    basket_path = Path("user_data/current_volume_basket.json")

    def _load_basket(self) -> dict:
        try:
            return json.loads(self.basket_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"long_pairs": [], "short_pairs": []}

    def _side_for_pair(self, pair: str) -> str:
        basket = self._load_basket()
        if pair in basket.get("long_pairs", []):
            return "long"
        if pair in basket.get("short_pairs", []):
            return "short"
        return "none"

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["enter_long"] = 0
        dataframe["enter_short"] = 0
        dataframe["enter_tag"] = None

        if dataframe.empty:
            return dataframe

        side = self._side_for_pair(metadata["pair"])
        last_index = dataframe.index[-1]

        if side == "long":
            dataframe.loc[last_index, "enter_long"] = 1
            dataframe.loc[last_index, "enter_tag"] = "current_volume_rank_top10"
        elif side == "short":
            dataframe.loc[last_index, "enter_short"] = 1
            dataframe.loc[last_index, "enter_tag"] = "current_volume_rank_tail10"

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["exit_long"] = 0
        dataframe["exit_short"] = 0
        return dataframe

    def custom_exit(
        self,
        pair: str,
        trade,
        current_time,
        current_rate: float,
        current_profit: float,
        **kwargs,
    ):
        if current_time >= trade.open_date_utc + timedelta(days=self.holding_days):
            return "time_exit"
        return None

