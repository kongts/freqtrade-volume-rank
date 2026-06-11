#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/freqtrade

python3 - <<'PY'
import json
from pathlib import Path

config = json.loads(Path("user_data/config-factor-testnet.json").read_text())
exchange = config.get("exchange", {})
if not exchange.get("key") or not exchange.get("secret"):
    raise SystemExit("Missing Binance Futures Testnet key/secret in user_data/config-factor-testnet.json")
if config.get("dry_run") is not False:
    raise SystemExit("config-factor-testnet.json must use dry_run=false for testnet orders")
if exchange.get("sandbox") is not True:
    raise SystemExit("config-factor-testnet.json must use exchange.sandbox=true")
PY

python3 user_data/scripts/update_factor_portfolio_basket.py

docker rm -f freqtrade-factor-testnet 2>/dev/null || true
docker run -d \
  --name freqtrade-factor-testnet \
  --restart unless-stopped \
  -v /home/ubuntu/freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable \
  trade \
  --logfile /freqtrade/user_data/logs/factor-testnet.log \
  --db-url sqlite:////freqtrade/user_data/trades_factor_testnet.sqlite \
  --config /freqtrade/user_data/config-factor-testnet.json \
  --strategy FactorPortfolioTriggerStrategy

docker logs --tail 80 freqtrade-factor-testnet
