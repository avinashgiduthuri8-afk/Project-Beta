"""Securities Master Database Manager for SQLite & relational storage."""

from __future__ import annotations

import sqlite3
import logging
from datetime import date, datetime
from typing import List, Dict, Any, Optional, Tuple
import pandas as pd

from sector_quant.db.schema import (
    ExchangeType,
    DataVendorType,
    SectorType,
    SymbolType,
    DailyPriceRecord,
    CorporateActionRecord,
    CREATE_TABLES_SQL,
)
from sector_quant.db.adjustments import calculate_corporate_action_adjustments

logger = logging.getLogger(__name__)

DEFAULT_SECTORS = [
    {"code": "ENERGY", "name": "Energy Sector", "benchmark_symbol": "XLE", "description": "Oil, Gas & Consumable Fuels"},
    {"code": "TECH", "name": "Technology Sector", "benchmark_symbol": "XLK", "description": "Software, Semiconductors & Hardware"},
    {"code": "FINANCIALS", "name": "Financial Services", "benchmark_symbol": "XLF", "description": "Banks, Insurance, Capital Markets"},
    {"code": "HEALTHCARE", "name": "Healthcare", "benchmark_symbol": "XLV", "description": "Pharmaceuticals & Biotechnology"},
    {"code": "AUTO", "name": "Automobiles", "benchmark_symbol": "NIFTY AUTO", "description": "Automobile & Auto Ancillary"},
    {"code": "FMCG", "name": "Fast Moving Consumer Goods", "benchmark_symbol": "XLP", "description": "Consumer Staples & Food"},
    {"code": "METALS", "name": "Metals & Mining", "benchmark_symbol": "NIFTY METAL", "description": "Iron, Steel & Base Metals"},
    {"code": "INFRA", "name": "Infrastructure", "benchmark_symbol": "XLI", "description": "Construction, Industrials & Utilities"},
]

DEFAULT_EXCHANGES = [
    {"code": "NYSE", "name": "New York Stock Exchange", "currency": "USD", "timezone": "America/New_York"},
    {"code": "NASDAQ", "name": "NASDAQ Stock Market", "currency": "USD", "timezone": "America/New_York"},
    {"code": "NSE", "name": "National Stock Exchange of India", "currency": "INR", "timezone": "Asia/Kolkata"},
    {"code": "BSE", "name": "Bombay Stock Exchange", "currency": "INR", "timezone": "Asia/Kolkata"},
    {"code": "SIMULATED", "name": "Simulated Backtest Exchange", "currency": "USD", "timezone": "UTC"},
]


class SecuritiesMaster:
    """Securities Master database interface for symbols, sectors, prices, and corporate actions."""

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        """Returns an active SQLite database connection with row factory."""
        if self._conn is None or self.db_path == ":memory:":
            if self._conn is None:
                self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
            return self._conn
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        """Initializes database schema and populates default exchanges and sectors."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executescript(CREATE_TABLES_SQL)
        conn.commit()

        # Seed default exchanges
        for ex in DEFAULT_EXCHANGES:
            cursor.execute(
                """INSERT OR IGNORE INTO exchange (code, name, currency, timezone)
                   VALUES (?, ?, ?, ?)""",
                (ex["code"], ex["name"], ex["currency"], ex["timezone"]),
            )

        # Seed default sectors
        for sec in DEFAULT_SECTORS:
            cursor.execute(
                """INSERT OR IGNORE INTO sector (code, name, benchmark_symbol, description)
                   VALUES (?, ?, ?, ?)""",
                (sec["code"], sec["name"], sec["benchmark_symbol"], sec["description"]),
            )

        conn.commit()

    # ---------------- Exchange & Vendor Operations ----------------
    def register_exchange(self, code: str, name: str, currency: str = "USD", timezone: str = "UTC") -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO exchange (code, name, currency, timezone)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name, currency=excluded.currency, timezone=excluded.timezone""",
            (code.upper(), name, currency, timezone),
        )
        conn.commit()
        cursor.execute("SELECT id FROM exchange WHERE code = ?", (code.upper(),))
        row = cursor.fetchone()
        return row[0]

    def get_exchange_id(self, code: str) -> Optional[int]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM exchange WHERE code = ?", (code.upper(),))
        row = cursor.fetchone()
        return row[0] if row else None

    def register_vendor(self, name: str, website_url: str = "", support_email: str = "") -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO data_vendor (name, website_url, support_email)
               VALUES (?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET website_url=excluded.website_url""",
            (name.upper(), website_url, support_email),
        )
        conn.commit()
        cursor.execute("SELECT id FROM data_vendor WHERE name = ?", (name.upper(),))
        return cursor.fetchone()[0]

    # ---------------- Sector Operations ----------------
    def register_sector(self, code: str, name: str, benchmark_symbol: Optional[str] = None, description: Optional[str] = None) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO sector (code, name, benchmark_symbol, description)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(code) DO UPDATE SET name=excluded.name, benchmark_symbol=excluded.benchmark_symbol, description=excluded.description""",
            (code.upper(), name, benchmark_symbol, description),
        )
        conn.commit()
        cursor.execute("SELECT id FROM sector WHERE code = ?", (code.upper(),))
        return cursor.fetchone()[0]

    def get_sector(self, code: str) -> Optional[SectorType]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, code, name, benchmark_symbol, description FROM sector WHERE code = ?", (code.upper(),))
        row = cursor.fetchone()
        if not row:
            return None
        return SectorType(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            benchmark_symbol=row["benchmark_symbol"],
            description=row["description"],
        )

    def list_sectors(self) -> List[SectorType]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, code, name, benchmark_symbol, description FROM sector ORDER BY code")
        return [
            SectorType(
                id=r["id"],
                code=r["code"],
                name=r["name"],
                benchmark_symbol=r["benchmark_symbol"],
                description=r["description"],
            )
            for r in cursor.fetchall()
        ]

    # ---------------- Symbol Operations ----------------
    def register_symbol(
        self,
        ticker: str,
        exchange_code: str = "NYSE",
        sector_code: Optional[str] = None,
        security_name: str = "",
        currency: str = "USD",
        is_active: bool = True,
    ) -> int:
        conn = self.get_connection()
        cursor = conn.cursor()

        ex_id = self.get_exchange_id(exchange_code)
        if ex_id is None:
            ex_id = self.register_exchange(exchange_code, exchange_code, currency)

        sec_id = None
        if sector_code:
            sec = self.get_sector(sector_code)
            if sec:
                sec_id = sec.id
            else:
                sec_id = self.register_sector(sector_code, f"{sector_code} Sector")

        cursor.execute(
            """INSERT INTO symbol (ticker, exchange_id, sector_id, security_name, currency, is_active)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(ticker) DO UPDATE SET
                   exchange_id=excluded.exchange_id,
                   sector_id=COALESCE(excluded.sector_id, symbol.sector_id),
                   security_name=excluded.security_name,
                   currency=excluded.currency,
                   is_active=excluded.is_active""",
            (ticker.upper(), ex_id, sec_id, security_name or ticker.upper(), currency, 1 if is_active else 0),
        )
        conn.commit()
        cursor.execute("SELECT id FROM symbol WHERE ticker = ?", (ticker.upper(),))
        return cursor.fetchone()[0]

    def get_symbol(self, ticker: str) -> Optional[SymbolType]:
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, ticker, exchange_id, sector_id, security_name, currency, is_active FROM symbol WHERE ticker = ?", (ticker.upper(),))
        r = cursor.fetchone()
        if not r:
            return None
        return SymbolType(
            id=r["id"],
            ticker=r["ticker"],
            exchange_id=r["exchange_id"],
            sector_id=r["sector_id"],
            security_name=r["security_name"],
            currency=r["currency"],
            is_active=bool(r["is_active"]),
        )

    def get_sector_constituents(self, sector_code: str) -> List[SymbolType]:
        """Returns all symbols belonging to a specific sector."""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """SELECT s.id, s.ticker, s.exchange_id, s.sector_id, s.security_name, s.currency, s.is_active
               FROM symbol s
               JOIN sector sec ON s.sector_id = sec.id
               WHERE sec.code = ? AND s.is_active = 1
               ORDER BY s.ticker""",
            (sector_code.upper(),),
        )
        return [
            SymbolType(
                id=r["id"],
                ticker=r["ticker"],
                exchange_id=r["exchange_id"],
                sector_id=r["sector_id"],
                security_name=r["security_name"],
                currency=r["currency"],
                is_active=bool(r["is_active"]),
            )
            for r in cursor.fetchall()
        ]

    # ---------------- Price Data Operations ----------------
    def insert_daily_prices(self, ticker: str, price_df: pd.DataFrame) -> int:
        """Inserts or updates daily price records for a symbol."""
        sym = self.get_symbol(ticker)
        if not sym:
            sym_id = self.register_symbol(ticker)
        else:
            sym_id = sym.id

        df = price_df.copy()
        if not isinstance(df.index, pd.DatetimeIndex) and "date" in df.columns:
            df["price_date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
        elif isinstance(df.index, pd.DatetimeIndex):
            df["price_date"] = df.index.strftime("%Y-%m-%d")
        elif "price_date" not in df.columns:
            raise ValueError("DataFrame must contain 'date' column or DatetimeIndex")

        # Map column names
        open_col = "open" if "open" in df.columns else "open_price"
        high_col = "high" if "high" in df.columns else "high_price"
        low_col = "low" if "low" in df.columns else "low_price"
        close_col = "close" if "close" in df.columns else "close_price"
        adj_close_col = "adj_close" if "adj_close" in df.columns else ("adj_close_price" if "adj_close_price" in df.columns else close_col)
        vol_col = "volume" if "volume" in df.columns else ("vol" if "vol" in df.columns else None)
        factor_col = "adj_factor" if "adj_factor" in df.columns else None

        records = []
        for _, row in df.iterrows():
            p_date = str(row["price_date"])[:10]
            o = float(row[open_col])
            h = float(row[high_col])
            l = float(row[low_col])
            c = float(row[close_col])
            ac = float(row[adj_close_col])
            v = int(row[vol_col]) if vol_col else 0
            af = float(row[factor_col]) if factor_col else 1.0
            records.append((sym_id, p_date, o, h, l, c, ac, v, af))

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.executemany(
            """INSERT INTO daily_price (symbol_id, price_date, open_price, high_price, low_price, close_price, adj_close_price, volume, adj_factor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(symbol_id, price_date) DO UPDATE SET
                   open_price=excluded.open_price,
                   high_price=excluded.high_price,
                   low_price=excluded.low_price,
                   close_price=excluded.close_price,
                   adj_close_price=excluded.adj_close_price,
                   volume=excluded.volume,
                   adj_factor=excluded.adj_factor""",
            records,
        )
        conn.commit()
        return len(records)

    def insert_corporate_action(
        self,
        ticker: str,
        ex_date: str,
        action_type: str,
        value: float,
        split_ratio: float = 1.0,
        cash_amount: float = 0.0,
        notes: str = "",
    ) -> int:
        sym = self.get_symbol(ticker)
        if not sym:
            sym_id = self.register_symbol(ticker)
        else:
            sym_id = sym.id

        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """INSERT INTO corporate_action (symbol_id, ex_date, action_type, value, split_ratio, cash_amount, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sym_id, str(ex_date)[:10], action_type.upper(), value, split_ratio, cash_amount, notes),
        )
        conn.commit()
        return cursor.lastrowid

    def get_daily_prices(
        self,
        ticker: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adjusted: bool = True,
    ) -> pd.DataFrame:
        """Retrieves historical daily prices for a ticker as a DataFrame."""
        sym = self.get_symbol(ticker)
        if not sym:
            return pd.DataFrame()

        conn = self.get_connection()
        query = """
            SELECT price_date, open_price, high_price, low_price, close_price, adj_close_price, volume, adj_factor
            FROM daily_price
            WHERE symbol_id = ?
        """
        params: List[Any] = [sym.id]

        if start_date:
            query += " AND price_date >= ?"
            params.append(str(start_date)[:10])
        if end_date:
            query += " AND price_date <= ?"
            params.append(str(end_date)[:10])

        query += " ORDER BY price_date ASC"

        df = pd.read_sql_query(query, conn, params=params)
        if df.empty:
            return df

        df["price_date"] = pd.to_datetime(df["price_date"])
        df.set_index("price_date", inplace=True)
        df.index.name = "date"

        if adjusted:
            df["open"] = df["open_price"] * df["adj_factor"]
            df["high"] = df["high_price"] * df["adj_factor"]
            df["low"] = df["low_price"] * df["adj_factor"]
            df["close"] = df["adj_close_price"]
            df["volume"] = df["volume"]
        else:
            df["open"] = df["open_price"]
            df["high"] = df["high_price"]
            df["low"] = df["low_price"]
            df["close"] = df["close_price"]
            df["volume"] = df["volume"]

        return df

    def get_sector_prices_matrix(
        self,
        sector_code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        price_col: str = "adj_close_price",
    ) -> pd.DataFrame:
        """Retrieves pivot table of adjusted close prices for all symbols in a sector."""
        constituents = self.get_sector_constituents(sector_code)
        if not constituents:
            return pd.DataFrame()

        dfs = {}
        for sym in constituents:
            df = self.get_daily_prices(sym.ticker, start_date=start_date, end_date=end_date, adjusted=True)
            if not df.empty:
                dfs[sym.ticker] = df["close"]

        if not dfs:
            return pd.DataFrame()

        matrix = pd.DataFrame(dfs)
        matrix.dropna(inplace=True)
        return matrix

