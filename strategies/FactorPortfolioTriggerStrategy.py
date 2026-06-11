"""
Factor portfolio trigger strategy for Binance USDT-M futures.

The portfolio logic is computed by user_data/scripts/update_factor_portfolio_basket.py.
This strategy intentionally stays thin:
    - Enter long/short pairs when the external basket enables entries.
    - Exit open positions when the external basket enables portfolio take-profit.
    - No stoploss in normal operation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pandas import DataFrame

from freqtrade.strategy import IStrategy


class FactorPortfolioTriggerStrategy(IStrategy):
    INTERFACE_VERSION = 3

    timeframe = "2h"
    can_short = True

    startup_candle_count = 1
    process_only_new_candles = True

    minimal_roi = {"0": 100}
    stoploss = -0.99
    use_custom_stoploss = False
    use_exit_signal = False
    exit_profit_only = False
    ignore_roi_if_entry_signal = True

    basket_path = Path("user_data/current_factor_portfolio_basket.json")

    def _load_basket(self) -> dict[str, Any]:
        try:
            return json.loads(self.basket_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {
                "entries_enabled": False,
                "exit_enabled": False,
                "long_pairs": [],
                "short_pairs": [],
            }

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

        basket = self._load_basket()
        if not basket.get("entries_enabled", False):
            return dataframe

        side = self._side_for_pair(metadata["pair"])
        last_index = dataframe.index[-1]
        sequence = basket.get("trade_sequence", 0)

        if side == "long":
            dataframe.loc[last_index, "enter_long"] = 1
            dataframe.loc[last_index, "enter_tag"] = f"drawdown60_top10_seq{sequence}"
        elif side == "short":
            dataframe.loc[last_index, "enter_short"] = 1
            dataframe.loc[last_index, "enter_tag"] = f"lowqv_weakbuy_seq{sequence}"

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
        basket = self._load_basket()
        if basket.get("exit_enabled", False):
            return "portfolio_take_profit"
        return None

    def leverage(
        self,
        pair: str,
        current_time,
        current_rate: float,
        proposed_leverage: float,
        max_leverage: float,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> float:
        return min(1.0, max_leverage)
