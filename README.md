# Project Beta

Project Beta is a local-first Indian-equities clone of Project Alpha. It keeps
the scanner, paper execution, live execution, learning, journaling, and risk
service boundaries while replacing the crypto market boundary with NSE/BSE
symbols and broker-neutral Indian order models.

## Safety defaults

- `BROKER=PAPER` and live trading disabled by default.
- Public/no-key Yahoo chart data is used for research and paper trading. It may
  be delayed and must not be treated as an execution-grade quote.
- Zerodha and Dhan live orders require credentials created specifically for
  Project Beta. No Alpha `.env`, token, database, cache, or API key is included.
- The live service refuses a `PAPER` broker when live trading is enabled.
- No VPS or cloud deployment is performed by this repository setup.

## Supported boundaries

| Capability | Provider | Credentials |
|---|---|---|
| NSE/BSE candles and delayed quotes | Yahoo public chart endpoint | None |
| Paper orders | Built-in in-memory adapter | None |
| Live orders | Zerodha Kite Connect v3 | Beta API key + daily access token |
| Live orders | DhanHQ v2 | Beta client ID + access token; static-IP rules may apply |
| Dhan instrument IDs | Public Dhan instrument master CSV | None |

Broker APIs do not offer anonymous live order placement. Public data mode and
paper mode work without keys; live execution is opt-in and authenticated.

## Market model

- Canonical symbols use `NSE:RELIANCE` or `BSE:500325` style identifiers.
- Cash-equity quantities are positive whole shares.
- The default tick is ₹0.05 and the default product is `CNC`.
- NSE/BSE regular session checks use Asia/Kolkata, 09:15–15:30 on weekdays.
- NIFTY 50 and BANK NIFTY replace BTC/ETH as regime benchmarks.
- Fee calculations estimate STT, stamp duty, transaction/SEBI charges, GST,
  brokerage, and slippage. Contract notes remain authoritative; income tax is
  intentionally not calculated.

## Local setup

```powershell
cd Project-Beta
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item config\beta.env.example .env
```

Run the scanner and paper service in separate terminals:

```powershell
python -m services.scanner.main
python -m services.paper_bot.main
```

## Optional broker configuration

Keep `TRADING_ENABLED=false` until paper verification is complete.

For Zerodha:

```dotenv
BROKER=ZERODHA
ZERODHA_API_KEY=
ZERODHA_ACCESS_TOKEN=
```

For Dhan:

```dotenv
BROKER=DHAN
DHAN_CLIENT_ID=
DHAN_ACCESS_TOKEN=
```

The access tokens are broker-session credentials and should never be committed.

## Verification

```powershell
python -m compileall -q services shared tests
pytest tests\beta -q
```

See `docs/INDIAN_MARKET_ADAPTATION.md` for API boundaries and limitations.
