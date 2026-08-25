"""
PROJECT-ALPHA Drawdown Circuit Breaker
Production safety system that automatically halts trading on excessive losses.

Limits:
- Daily Loss: 3% → Block new trades
- Weekly Loss: 8% → Pause trading
- Monthly Loss: 12% → Manual review mode
- Max Drawdown: 20% → EMERGENCY STOP

All thresholds are configurable via environment variables.
"""

import os
import json
import logging
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional, Tuple
from enum import Enum

logger = logging.getLogger("circuit_breaker")

# ============================================================
# LAZY ALERT MANAGER
# ============================================================

_alert_manager = None


def _get_alert_manager():
    """Lazy getter for AlertManager — fails silently so it never affects circuit breaker logic."""
    global _alert_manager
    if _alert_manager is None:
        try:
            from monitoring.telegram_alerts import AlertManager
            _alert_manager = AlertManager()
        except Exception:
            pass
    return _alert_manager

# ============================================================
# CONFIGURATION
# ============================================================

DAILY_LOSS_LIMIT_PCT = float(os.getenv("DAILY_LOSS_LIMIT_PCT", "3.0"))
WEEKLY_LOSS_LIMIT_PCT = float(os.getenv("WEEKLY_LOSS_LIMIT_PCT", "8.0"))
MONTHLY_LOSS_LIMIT_PCT = float(os.getenv("MONTHLY_LOSS_LIMIT_PCT", "12.0"))
MAX_DRAWDOWN_PCT = float(os.getenv("MAX_DRAWDOWN_PCT", "20.0"))

# Initial capital for drawdown calculation
INITIAL_CAPITAL = float(os.getenv("INITIAL_CAPITAL", "1000000"))

# Circuit breaker state file
CIRCUIT_BREAKER_FILE = Path(os.getenv(
    "CIRCUIT_BREAKER_FILE",
    str(Path(__file__).parent / "data" / "circuit_breaker.json")
))


class TradingState(Enum):
    """Trading system states based on circuit breaker status."""
    ACTIVE = "ACTIVE"                    # Normal trading
    DAILY_LIMIT_HIT = "DAILY_LIMIT"      # Daily loss limit reached
    WEEKLY_LIMIT_HIT = "WEEKLY_LIMIT"    # Weekly loss limit reached  
    MONTHLY_LIMIT_HIT = "MONTHLY_LIMIT"  # Monthly - manual review required
    EMERGENCY_STOP = "EMERGENCY_STOP"    # Max drawdown - all trading halted
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"  # Admin has manually paused


@dataclass
class CircuitBreakerState:
    """Persistent state for the circuit breaker."""
    # Current state
    trading_state: str = "ACTIVE"
    
    # Peak equity tracking
    peak_equity: float = INITIAL_CAPITAL
    current_equity: float = INITIAL_CAPITAL
    
    # Loss tracking
    daily_pnl: float = 0.0
    weekly_pnl: float = 0.0
    monthly_pnl: float = 0.0
    
    # Timestamps
    daily_reset_time: str = ""
    weekly_reset_time: str = ""
    monthly_reset_time: str = ""
    last_update: str = ""
    
    # Trigger history
    daily_limit_triggered_at: Optional[str] = None
    weekly_limit_triggered_at: Optional[str] = None
    monthly_limit_triggered_at: Optional[str] = None
    emergency_stop_triggered_at: Optional[str] = None
    
    # Statistics
    total_trades_blocked: int = 0
    circuit_breaks_count: int = 0


# ============================================================
# CIRCUIT BREAKER ENGINE
# ============================================================

class CircuitBreaker:
    """
    Production-grade circuit breaker for trading system safety.
    
    Automatically halts trading when loss limits are exceeded:
    - Daily 3%: Block new trades for remainder of day
    - Weekly 8%: Pause all trading until next week
    - Monthly 12%: Require manual review/reset
    - 20% Drawdown: EMERGENCY STOP - all trading halted
    """
    
    def __init__(self, initial_capital: float = INITIAL_CAPITAL):
        self.initial_capital = initial_capital
        self.state = self._load_state()
        self._ensure_reset_times()
    
    def _load_state(self) -> CircuitBreakerState:
        """Load state from persistent storage."""
        if not CIRCUIT_BREAKER_FILE.exists():
            return CircuitBreakerState(
                peak_equity=self.initial_capital,
                current_equity=self.initial_capital
            )
        try:
            with open(CIRCUIT_BREAKER_FILE, "r") as f:
                data = json.load(f)
            return CircuitBreakerState(**data)
        except (json.JSONDecodeError, TypeError, OSError) as e:
            logger.warning("Circuit breaker state load failed: %s", e)
            return CircuitBreakerState(
                peak_equity=self.initial_capital,
                current_equity=self.initial_capital
            )
    
    def _save_state(self) -> None:
        """Persist state to file."""
        CIRCUIT_BREAKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        self.state.last_update = datetime.now(timezone.utc).isoformat()
        
        # Atomic write
        tmp_file = CIRCUIT_BREAKER_FILE.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(asdict(self.state), f, indent=2)
        tmp_file.replace(CIRCUIT_BREAKER_FILE)
    
    def _ensure_reset_times(self) -> None:
        """Initialize or check reset times for PnL periods."""
        now = datetime.now(timezone.utc)
        
        # Daily reset at midnight UTC
        if not self.state.daily_reset_time:
            self.state.daily_reset_time = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
        
        # Weekly reset on Monday midnight UTC
        if not self.state.weekly_reset_time:
            days_since_monday = now.weekday()
            monday = now - timedelta(days=days_since_monday)
            self.state.weekly_reset_time = monday.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
        
        # Monthly reset on 1st of month
        if not self.state.monthly_reset_time:
            self.state.monthly_reset_time = now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
        
        self._check_period_resets()
    
    def _check_period_resets(self) -> None:
        """Reset PnL counters when periods roll over."""
        now = datetime.now(timezone.utc)
        
        # Check daily reset
        daily_reset = datetime.fromisoformat(self.state.daily_reset_time)
        if daily_reset.tzinfo is None:
            daily_reset = daily_reset.replace(tzinfo=timezone.utc)
        
        if now >= daily_reset + timedelta(days=1):
            logger.info("CIRCUIT_BREAKER: Daily PnL reset (was %.2f%%)", 
                       self.state.daily_pnl / self.initial_capital * 100)
            self.state.daily_pnl = 0.0
            self.state.daily_reset_time = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            
            # Clear daily limit if it was the only trigger
            if self.state.trading_state == TradingState.DAILY_LIMIT_HIT.value:
                self.state.trading_state = TradingState.ACTIVE.value
                self.state.daily_limit_triggered_at = None
                logger.info("CIRCUIT_BREAKER: Daily limit cleared, trading resumed")
        
        # Check weekly reset
        weekly_reset = datetime.fromisoformat(self.state.weekly_reset_time)
        if weekly_reset.tzinfo is None:
            weekly_reset = weekly_reset.replace(tzinfo=timezone.utc)
        
        if now >= weekly_reset + timedelta(weeks=1):
            logger.info("CIRCUIT_BREAKER: Weekly PnL reset (was %.2f%%)",
                       self.state.weekly_pnl / self.initial_capital * 100)
            self.state.weekly_pnl = 0.0
            days_since_monday = now.weekday()
            monday = now - timedelta(days=days_since_monday)
            self.state.weekly_reset_time = monday.replace(
                hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            
            if self.state.trading_state == TradingState.WEEKLY_LIMIT_HIT.value:
                self.state.trading_state = TradingState.ACTIVE.value
                self.state.weekly_limit_triggered_at = None
                logger.info("CIRCUIT_BREAKER: Weekly limit cleared, trading resumed")
        
        # Check monthly reset
        monthly_reset = datetime.fromisoformat(self.state.monthly_reset_time)
        if monthly_reset.tzinfo is None:
            monthly_reset = monthly_reset.replace(tzinfo=timezone.utc)
        
        next_month = (monthly_reset.replace(day=28) + timedelta(days=4)).replace(day=1)
        if now >= next_month:
            logger.info("CIRCUIT_BREAKER: Monthly PnL reset (was %.2f%%)",
                       self.state.monthly_pnl / self.initial_capital * 100)
            self.state.monthly_pnl = 0.0
            self.state.monthly_reset_time = now.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            ).isoformat()
            
            # Monthly limit requires manual reset - do NOT auto-clear
    
    def update_equity(self, current_equity: float) -> None:
        """
        Update current equity and track peak for drawdown calculation.
        Call this after every trade or balance change.
        """
        self.state.current_equity = current_equity
        
        # Update peak if new high
        if current_equity > self.state.peak_equity:
            self.state.peak_equity = current_equity
        
        self._save_state()
    
    def record_trade_pnl(self, pnl: float) -> TradingState:
        """
        Record a trade's PnL and check if any limits are breached.
        Returns the new trading state.
        """
        self._check_period_resets()
        
        # Update PnL counters
        self.state.daily_pnl += pnl
        self.state.weekly_pnl += pnl
        self.state.monthly_pnl += pnl
        self.state.current_equity += pnl
        
        # Update peak equity if positive
        if self.state.current_equity > self.state.peak_equity:
            self.state.peak_equity = self.state.current_equity
        
        # Check limits (most severe first)
        new_state = self._evaluate_limits()
        
        if new_state != TradingState.ACTIVE:
            self.state.circuit_breaks_count += 1
            logger.warning("CIRCUIT_BREAKER: State changed to %s", new_state.value)
        
        self.state.trading_state = new_state.value
        self._save_state()
        
        return new_state
    
    def _evaluate_limits(self) -> TradingState:
        """Evaluate all loss limits and return appropriate state."""
        now_iso = datetime.now(timezone.utc).isoformat()
        
        # 1. Check maximum drawdown (most severe)
        drawdown_pct = self._calculate_drawdown()
        if drawdown_pct >= MAX_DRAWDOWN_PCT:
            self.state.emergency_stop_triggered_at = now_iso
            logger.critical(
                "CIRCUIT_BREAKER: EMERGENCY STOP - Drawdown %.2f%% >= %.2f%%",
                drawdown_pct, MAX_DRAWDOWN_PCT
            )
            try:
                am = _get_alert_manager()
                if am:
                    am.alert_circuit_breaker_activated(
                        "EMERGENCY_STOP", {"drawdown_pct": drawdown_pct})
            except Exception:
                pass
            return TradingState.EMERGENCY_STOP
        
        # 2. Check monthly limit
        monthly_loss_pct = abs(self.state.monthly_pnl) / self.initial_capital * 100
        if self.state.monthly_pnl < 0 and monthly_loss_pct >= MONTHLY_LOSS_LIMIT_PCT:
            self.state.monthly_limit_triggered_at = now_iso
            logger.error(
                "CIRCUIT_BREAKER: Monthly limit hit - Loss %.2f%% >= %.2f%%",
                monthly_loss_pct, MONTHLY_LOSS_LIMIT_PCT
            )
            return TradingState.MONTHLY_LIMIT_HIT
        
        # 3. Check weekly limit
        weekly_loss_pct = abs(self.state.weekly_pnl) / self.initial_capital * 100
        if self.state.weekly_pnl < 0 and weekly_loss_pct >= WEEKLY_LOSS_LIMIT_PCT:
            self.state.weekly_limit_triggered_at = now_iso
            logger.warning(
                "CIRCUIT_BREAKER: Weekly limit hit - Loss %.2f%% >= %.2f%%",
                weekly_loss_pct, WEEKLY_LOSS_LIMIT_PCT
            )
            return TradingState.WEEKLY_LIMIT_HIT
        
        # 4. Check daily limit
        daily_loss_pct = abs(self.state.daily_pnl) / self.initial_capital * 100
        if self.state.daily_pnl < 0 and daily_loss_pct >= DAILY_LOSS_LIMIT_PCT:
            self.state.daily_limit_triggered_at = now_iso
            logger.warning(
                "CIRCUIT_BREAKER: Daily limit hit - Loss %.2f%% >= %.2f%%",
                daily_loss_pct, DAILY_LOSS_LIMIT_PCT
            )
            try:
                am = _get_alert_manager()
                if am:
                    am.alert_daily_loss_limit(daily_loss_pct, DAILY_LOSS_LIMIT_PCT)
            except Exception:
                pass
            return TradingState.DAILY_LIMIT_HIT
        
        return TradingState.ACTIVE
    
    def _calculate_drawdown(self) -> float:
        """Calculate current drawdown percentage from peak equity."""
        if self.state.peak_equity <= 0:
            return 0.0
        drawdown = (self.state.peak_equity - self.state.current_equity) / self.state.peak_equity * 100
        return max(0.0, drawdown)
    
    def can_trade(self) -> Tuple[bool, str]:
        """
        Check if trading is allowed based on current state.
        Returns (allowed: bool, reason: str)
        """
        # FIX: Emergency Stop Verification - V1
        # Check global EMERGENCY_STOP env var first
        import os
        if os.getenv("EMERGENCY_STOP", "false").lower() == "true":
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, "EMERGENCY_STOP=true (global emergency stop active)"
        
        self._check_period_resets()
        
        state = TradingState(self.state.trading_state)
        
        if state == TradingState.ACTIVE:
            return True, "Trading active"
        
        if state == TradingState.DAILY_LIMIT_HIT:
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, f"Daily loss limit ({DAILY_LOSS_LIMIT_PCT}%) reached. Resumes at next day."
        
        if state == TradingState.WEEKLY_LIMIT_HIT:
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, f"Weekly loss limit ({WEEKLY_LOSS_LIMIT_PCT}%) reached. Resumes next week."
        
        if state == TradingState.MONTHLY_LIMIT_HIT:
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, f"Monthly loss limit ({MONTHLY_LOSS_LIMIT_PCT}%) reached. Manual review required."
        
        if state == TradingState.EMERGENCY_STOP:
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, f"EMERGENCY STOP: Max drawdown ({MAX_DRAWDOWN_PCT}%) exceeded. Admin intervention required."
        
        if state == TradingState.MANUAL_OVERRIDE:
            self.state.total_trades_blocked += 1
            self._save_state()
            return False, "Trading manually paused by admin."
        
        return False, "Unknown circuit breaker state"
    
    def manual_reset(self, admin_key: str = "") -> bool:
        """
        Manually reset circuit breaker (admin function).
        For monthly limit and emergency stop recovery.
        """
        # In production, verify admin_key against secure storage
        logger.warning("CIRCUIT_BREAKER: Manual reset initiated")
        
        self.state.trading_state = TradingState.ACTIVE.value
        self.state.daily_limit_triggered_at = None
        self.state.weekly_limit_triggered_at = None
        self.state.monthly_limit_triggered_at = None
        self.state.emergency_stop_triggered_at = None
        
        # Reset peak to current (fresh start)
        self.state.peak_equity = self.state.current_equity
        
        self._save_state()
        logger.info("CIRCUIT_BREAKER: Manual reset complete - trading resumed")
        try:
            am = _get_alert_manager()
            if am:
                am.alert_circuit_breaker_reset()
        except Exception:
            pass
        return True
    
    def get_status(self) -> dict:
        """Return current circuit breaker status for monitoring."""
        self._check_period_resets()
        
        return {
            "trading_state": self.state.trading_state,
            "can_trade": self.can_trade()[0],
            "reason": self.can_trade()[1],
            "current_equity": round(self.state.current_equity, 2),
            "peak_equity": round(self.state.peak_equity, 2),
            "drawdown_pct": round(self._calculate_drawdown(), 2),
            "daily_pnl": round(self.state.daily_pnl, 2),
            "daily_pnl_pct": round(self.state.daily_pnl / self.initial_capital * 100, 2),
            "weekly_pnl": round(self.state.weekly_pnl, 2),
            "weekly_pnl_pct": round(self.state.weekly_pnl / self.initial_capital * 100, 2),
            "monthly_pnl": round(self.state.monthly_pnl, 2),
            "monthly_pnl_pct": round(self.state.monthly_pnl / self.initial_capital * 100, 2),
            "limits": {
                "daily": f"{DAILY_LOSS_LIMIT_PCT}%",
                "weekly": f"{WEEKLY_LOSS_LIMIT_PCT}%",
                "monthly": f"{MONTHLY_LOSS_LIMIT_PCT}%",
                "max_drawdown": f"{MAX_DRAWDOWN_PCT}%",
            },
            "total_trades_blocked": self.state.total_trades_blocked,
            "circuit_breaks_count": self.state.circuit_breaks_count,
            "last_update": self.state.last_update,
        }


# ============================================================
# GLOBAL INSTANCE
# ============================================================

_circuit_breaker: Optional[CircuitBreaker] = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get or create the global circuit breaker instance."""
    global _circuit_breaker
    if _circuit_breaker is None:
        _circuit_breaker = CircuitBreaker()
    return _circuit_breaker


def check_can_trade() -> Tuple[bool, str]:
    """Convenience function to check if trading is allowed."""
    return get_circuit_breaker().can_trade()


def record_pnl(pnl: float) -> TradingState:
    """Convenience function to record trade PnL."""
    return get_circuit_breaker().record_trade_pnl(pnl)


def get_breaker_status() -> dict:
    """Convenience function to get circuit breaker status."""
    return get_circuit_breaker().get_status()
