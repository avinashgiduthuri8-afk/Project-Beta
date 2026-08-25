# Project-Beta: Indian Equities & Derivatives (NSE/BSE) Execution Trading Bot

**Project-Beta** is a high-performance, modular algorithmic execution trading bot built for the Indian stock market (NSE/BSE Cash & F&O). It includes automated 2FA/TOTP session authentication, exchange order routing (`MIS`/`CNC`/`NRML`), an intraday Risk Management System (RMS) with strict IST session guards (09:15–15:15 IST), real-time WebSocket tick-to-candle resampling, and automated intraday square-off.

---

## 🏛 Architecture

```text
Project-Beta/
├── config/
│   ├── settings.yaml            # Market clock, RMS limits, symbols, notifications
│   ├── .env.example             # API keys, TOTP secrets, tokens template
│   └── config_loader.py         # Pydantic schema validation & environment loader
│
├── core/
│   ├── enums.py                 # Exchange (NSE/BSE/NFO), ProductType (MIS/CNC/NRML), OrderType
│   ├── models.py                # Tick, Candle, Order, Trade, Position, AccountBalance
│   └── interfaces.py            # BaseBroker, BaseRiskEngine, BaseStrategy, BaseNotifier
│
├── auth/
│   ├── session_manager.py       # Automated TOTP (pyotp) 2FA login & token lifecycle
│   └── token_cache.py           # Secure token caching with daily auto-invalidation
│
├── brokers/
│   ├── base.py                  # Abstract Broker Interface
│   ├── paper_broker.py          # Zero-risk virtual simulator with realistic fills & P&L
│   ├── zerodha_kite.py          # Zerodha Kite Connect API adapter
│   ├── angel_one.py             # Angel One SmartAPI adapter
│   └── dhan.py                  # DhanHQ API adapter
│
├── oms/
│   ├── execution_router.py      # Tick-size (₹0.05) & lot-size normalizer, retry mechanics
│   ├── order_manager.py         # In-memory order state machine (PENDING -> COMPLETE/REJECTED)
│   └── order_book_syncer.py     # Background thread synchronizing with broker order book
│
├── risk/
│   ├── market_clock.py          # IST Timezone Market Guard (09:00 Pre-open, 09:15 Open, 15:15 Square-off, 15:30 Close)
│   ├── risk_engine.py           # RMS Pre-trade checks, Daily Max Loss Circuit Breaker
│   ├── rate_limiter.py          # Token-bucket rate limiter (5 orders/sec API throttle)
│   └── position_sizer.py        # Margin & risk-distance position sizer with F&O lot sizing
│
├── data/
│   ├── ticker.py                # Real-time WebSocket ticker consumer with auto-reconnect backoff
│   ├── candle_builder.py        # Live tick-to-candle resampler (1m / 5m / 15m OHLCV)
│   └── event_bus.py             # Thread-safe pub/sub event dispatcher
│
├── storage/
│   ├── database.py              # SQLite repository for orders, trades, and P&L snapshots
│   └── journal.py               # Trade audit journal with CSV export and daily summaries
│
├── notifications/
│   ├── telegram.py              # Telegram Bot alerts (order fills, SL hits, RMS alerts)
│   └── discord.py               # Discord Webhook alerts
│
├── strategies/
│   ├── base_strategy.py         # Strategy base class with helper order methods
│   └── sample_vwap_momentum.py  # Production-ready sample VWAP breakout strategy
│
├── main.py                      # Master orchestrator & IST market lifecycle supervisor
├── pyproject.toml               # Dependencies & project metadata
└── tests/                       # Complete unit & integration test suite
```

---

## ⚡ Quick Start

### 1. Installation

```bash
# Clone and navigate into Project-Beta
cd Project-Beta

# Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate # Linux/Mac

# Install dependencies
pip install -e .[dev]
```

### 2. Configure Environment

Copy `config/.env.example` to `.env` and configure your credentials:

```bash
cp config/.env.example .env
```

Set `TRADING_MODE=PAPER` to test without live funds, or choose your broker:
- `ACTIVE_BROKER=PAPER`
- `ACTIVE_BROKER=ZERODHA`
- `ACTIVE_BROKER=ANGEL_ONE`
- `ACTIVE_BROKER=DHAN`

### 3. Run Automated Tests

```bash
pytest tests/ -v
```

### 4. Start the Execution Bot

```bash
python main.py
```

---

## 🛡 Risk Management (RMS) & Indian Market Rules

1. **Strict IST Market Guard**:
   - **09:00 - 09:08 IST**: Pre-open discovery.
   - **09:15 - 15:15 IST**: Active trading session. Orders outside this window are blocked.
   - **15:15 IST**: Automated intraday square-off triggers. Open pending `MIS` orders are cancelled, and open positions are closed before exchange penalties apply.
   - **15:30 IST**: Market close & daily P&L reporting.

2. **Daily Loss Circuit Breaker**:
   - Automatically halts further trade placement if cumulative daily loss reaches `max_daily_loss_inr` (e.g. -₹5,000).

3. **Tick & Lot Normalization**:
   - Price inputs automatically round to exact ₹0.05 multiples.
   - Derivative order quantities automatically quantize to exchange lot sizes (e.g., NIFTY 50, BANKNIFTY 15).

4. **API Rate Limiting**:
   - Token-bucket rate limiter throttles outbound requests to 5 orders/sec to prevent broker IP bans.

---

## 🔔 Notifications & Trade Journaling

- **Telegram / Discord**: Real-time alerts on order submission, execution fills, stop-loss hits, and RMS circuit breaker triggers.
- **SQLite Database**: Persisted at `data/project_beta.db`.
- **CSV Trade Journal**: Exported at `data/trade_journal.csv` for audit compliance.
