#!/usr/bin/env bash
set -euo pipefail

cd /home/ubuntu/freqtrade

ENV_FILE="/home/ubuntu/freqtrade/user_data/factor-testnet.env"

python3 - <<'PY'
import json
import os
from pathlib import Path

config = json.loads(Path("user_data/config-factor-testnet.json").read_text())
exchange = config.get("exchange", {})
if config.get("dry_run") is not False:
    raise SystemExit("config-factor-testnet.json must use dry_run=false for testnet orders")
if exchange.get("sandbox") is not True:
    raise SystemExit("config-factor-testnet.json must use exchange.sandbox=true")
PY

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'EOF'
# Binance Futures Testnet credentials only.
# Do not use production Binance API keys here.
FREQTRADE__EXCHANGE__KEY=
FREQTRADE__EXCHANGE__SECRET=
EOF
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE"
  echo "Fill FREQTRADE__EXCHANGE__KEY and FREQTRADE__EXCHANGE__SECRET, then rerun this script."
  exit 1
fi

set -a
source "$ENV_FILE"
set +a

if [[ -z "${FREQTRADE__EXCHANGE__KEY:-}" || -z "${FREQTRADE__EXCHANGE__SECRET:-}" ]]; then
  echo "Missing FREQTRADE__EXCHANGE__KEY or FREQTRADE__EXCHANGE__SECRET in $ENV_FILE"
  exit 1
fi

python3 user_data/scripts/update_factor_portfolio_basket.py

docker rm -f freqtrade-factor-testnet 2>/dev/null || true
docker run -d \
  --name freqtrade-factor-testnet \
  --restart unless-stopped \
  --env-file "$ENV_FILE" \
  -v /home/ubuntu/freqtrade/user_data:/freqtrade/user_data \
  freqtradeorg/freqtrade:stable \
  trade \
  --logfile /freqtrade/user_data/logs/factor-testnet.log \
  --db-url sqlite:////freqtrade/user_data/trades_factor_testnet.sqlite \
  --config /freqtrade/user_data/config-factor-testnet.json \
  --strategy FactorPortfolioTriggerStrategy

docker logs --tail 80 freqtrade-factor-testnet
