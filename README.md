# Project-Beta: Indian Equities & Derivatives (NSE/BSE) Execution Trading Bot

**Project-Beta** is a high-performance, modular algorithmic execution trading bot built specifically for the Indian stock market (NSE/BSE Equities & NFO/BFO Derivatives).

It features pure execution capabilities with zero market scanner overhead, including automated 2FA/TOTP session authentication, exchange order routing (`MIS`/`CNC`/`NRML`), an intraday Risk Management System (RMS) with strict IST session guards (09:15–15:15 IST), ₹0.05 tick size normalization, F&O lot sizing, real-time WebSocket tick-to-candle resampling, and automated intraday square-off.

---

## 🏛 Architecture Overview

```text
Project-Beta/
├── indian_stock_execution_bot.py     # 🌟 Self-contained standalone single-file execution bot (MTB, MRB, PMB)
│
├── execution/                        # Migrated Execution Engines
│   ├── mtb_bot.py                    # Momentum Trading Bot (ORB & VWAP Breakouts + Trailing SL)
│   ├── mrb_bot.py                    # Mean Reversion Bot (Counter-trend VWAP Fades & Bracket Scalping)
│   ├── pmb_bot.py                    # Portfolio Management Bot (Global RMS, 15:15 IST Square-off)
│   └── __init__.py                   # Package exports
│
├── config/
│   ├── settings.yaml                 # Market clock, RMS limits, symbols, notifications
│   ├── .env.example                  # API keys, TOTP secrets, tokens template
│   └── config_loader.py              # Pydantic schema validation & environment loader
│
├── core/
│   ├── enums.py                      # Exchange (NSE/BSE/NFO), ProductType (MIS/CNC/NRML), OrderType
│   ├── models.py                     # Tick, Candle, Order, Trade, Position, AccountBalance
│   └── interfaces.py                 # BaseBroker, BaseRiskEngine, BaseStrategy, BaseNotifier
│
├── auth/
│   ├── session_manager.py            # Automated TOTP (pyotp) 2FA login & token lifecycle
│   └── token_cache.py                # Secure token caching with daily auto-invalidation
│
├── brokers/
│   ├── base.py                       # Abstract Broker Interface
│   ├── paper_broker.py               # Zero-risk virtual simulator with realistic fills & P&L
│   ├── zerodha_kite.py               # Zerodha Kite Connect API adapter
│   ├── angel_one.py                  # Angel One SmartAPI adapter
│   ├── dhan.py                       # DhanHQ API adapter
│   └── __init__.py                   # get_broker() factory helper
│
├── oms/
│   ├── execution_router.py           # Tick-size (₹0.05) & lot-size normalizer, retry mechanics
│   ├── order_manager.py              # In-memory order state machine (PENDING -> COMPLETE/REJECTED)
│   └── order_book_syncer.py          # Background thread synchronizing with broker order book
│
├── risk/
│   ├── market_clock.py               # IST Timezone Market Guard (09:00 Pre-open, 09:15 Open, 15:15 Square-off, 15:30 Close)
│   ├── risk_engine.py                # RMS Pre-trade checks, Daily Max Loss Circuit Breaker
│   ├── rate_limiter.py               # Token-bucket rate limiter (5 orders/sec API throttle)
│   └── position_sizer.py             # Margin & risk-distance position sizer with F&O lot sizing
│
├── data/
│   ├── ticker.py                     # Real-time WebSocket ticker consumer with auto-reconnect backoff
│   ├── candle_builder.py             # Live tick-to-candle resampler (1m / 5m / 15m OHLCV)
│   └── event_bus.py                  # Thread-safe pub/sub event dispatcher
│
├── storage/
│   ├── database.py                   # SQLite repository for orders, trades, and P&L snapshots
│   └── journal.py                    # Trade audit journal with CSV export and daily summaries
│
├── notifications/
│   ├── telegram.py                   # Telegram Bot alerts (order fills, SL hits, RMS alerts)
│   └── discord.py                    # Discord Webhook alerts
│
├── strategies/
│   ├── base_strategy.py              # Strategy base class with helper order methods
│   └── sample_vwap_momentum.py       # Production-ready sample VWAP breakout strategy
│
├── main.py                           # Master orchestrator & IST market lifecycle supervisor
└── tests/                            # Complete unit & integration test suite (16 tests)
```

---

## 🤖 Execution Bot Engines (MTB, MRB, PMB)

| Bot Engine | Description & Target | Strategy / Execution Mechanics |
| :--- | :--- | :--- |
| **MTB (Momentum Trading Bot)** | Trend-following & breakout execution engine | • Opening Range Breakout (ORB)<br>• VWAP + Volume Surge triggers<br>• Dynamic ₹0.05 trailing stop-loss<br>• High 1:2+ R:R profit targets |
| **MRB (Mean Reversion Bot)** | Counter-trend & range-bound scalp engine | • Extended VWAP & Bollinger Band fade triggers<br>• RSI extreme reversals<br>• Bracket orders (fixed target + tight stop-loss)<br>• Time-stop bar limit exit |
| **PMB (Portfolio Management Bot)** | Account-wide RMS & position supervisor | • Aggregate P&L and drawdown monitoring<br>• Daily Max Loss circuit breaker<br>• Automated 15:15 IST MIS square-off<br>• Emergency liquidation kill-switch |

---

## 🚀 Quickstart & Usage

### 1. Standalone Single-File Execution Bot
The file `indian_stock_execution_bot.py` is a 100% self-contained script that can be run directly:

```bash
# Run all bot engines in paper trading mode
python indian_stock_execution_bot.py --bot all --mode paper --broker paper

# Run Momentum Trading Bot (MTB) only
python indian_stock_execution_bot.py --bot mtb --mode paper

# Run Mean Reversion Bot (MRB) only
python indian_stock_execution_bot.py --bot mrb --mode paper

# Run Portfolio Management Bot (PMB) only
python indian_stock_execution_bot.py --bot pmb --mode paper

# Perform an instant dry-run sanity check
python indian_stock_execution_bot.py --mode paper --broker paper --dry-run
```

### 2. Full Modular Orchestrator
```bash
# Run the master supervisor loop
python main.py --mode paper --broker paper
```

---

## 🧪 Automated Testing

Execute the test suite with `pytest`:

```bash
python -m pytest
```

All 16 tests covering authentication, OMS, tick normalization, lot sizing, market clock, circuit breakers, event bus, and MTB/MRB/PMB bots pass with 100% success.
