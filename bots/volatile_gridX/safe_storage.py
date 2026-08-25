"""
PROJECT-ALPHA Production-Grade Storage Module
Thread-safe storage with atomic writes, backup recovery, and corruption detection.

Replaces unsafe in-memory + bare JSON storage with production-ready implementation.
"""

import os
import json
import shutil
import hashlib
import logging
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

from .thread_safety import storage_lock, analytics_lock

logger = logging.getLogger("vgx.safe_storage")

# ============================================================
# CONFIGURATION
# ============================================================

DATA_DIR = Path(os.getenv("VGX_DATA_DIR", str(Path(__file__).parent / "data")))
BACKUP_DIR = DATA_DIR / "backups"

# Storage files
POSITIONS_FILE = DATA_DIR / "positions.json"
TRADE_HISTORY_FILE = DATA_DIR / "trade_history.json"
ANALYTICS_FILE = DATA_DIR / "analytics.json"
BOT_STATE_FILE = DATA_DIR / "TradingBotCrypto.json"

# Limits
MAX_TRADE_HISTORY = 10000
MAX_BACKUPS = 48  # 48 hours of hourly backups

# Checksum algorithm
CHECKSUM_ALGO = "sha256"


# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class StorageStats:
    """Storage statistics for monitoring."""
    positions_count: int = 0
    trade_history_count: int = 0
    total_file_size_kb: float = 0
    last_backup: Optional[str] = None
    corruption_detected: bool = False
    last_integrity_check: Optional[str] = None


# ============================================================
# CHECKSUM & INTEGRITY
# ============================================================

def calculate_checksum(data: dict) -> str:
    """Calculate checksum for data integrity verification."""
    content = json.dumps(data, sort_keys=True)
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def verify_checksum(data: dict, expected: str) -> bool:
    """Verify data integrity using stored checksum."""
    return calculate_checksum(data) == expected


def wrap_with_checksum(data: dict) -> dict:
    """Wrap data with metadata including checksum."""
    return {
        "data": data,
        "checksum": calculate_checksum(data),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0"
    }


def unwrap_with_verification(wrapped: dict) -> tuple:
    """
    Unwrap data and verify integrity.
    Returns (data, is_valid)
    """
    if "data" not in wrapped:
        # Legacy format without wrapper
        return wrapped, True
    
    data = wrapped.get("data", {})
    stored_checksum = wrapped.get("checksum", "")
    
    if not stored_checksum:
        return data, True  # No checksum = legacy, assume valid
    
    is_valid = verify_checksum(data, stored_checksum)
    return data, is_valid


# ============================================================
# ATOMIC FILE OPERATIONS
# ============================================================

def atomic_write_json(file_path: Path, data: dict, create_backup: bool = True) -> bool:
    """
    Atomically write JSON with backup and integrity checking.
    
    Process:
    1. Create backup of existing file
    2. Write to temp file
    3. Verify temp file is valid JSON
    4. Atomic rename
    """
    file_path = Path(file_path)
    
    try:
        # Ensure directories exist
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Create backup if file exists
        if create_backup and file_path.exists():
            backup_file = file_path.with_suffix(f".{int(time.time())}.bak")
            shutil.copy2(file_path, backup_file)
            
            # Also keep a .bak file for quick recovery
            shutil.copy2(file_path, file_path.with_suffix(".json.bak"))
        
        # Wrap data with checksum
        wrapped_data = wrap_with_checksum(data)
        
        # Write to temp file
        tmp_file = file_path.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(wrapped_data, f, indent=2, ensure_ascii=False)
        
        # Verify temp file is valid
        with open(tmp_file, "r", encoding="utf-8") as f:
            verification = json.load(f)
        
        if "data" not in verification:
            raise ValueError("Written file missing data wrapper")
        
        # Atomic rename
        tmp_file.replace(file_path)
        
        return True
        
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.error("Atomic write failed for %s: %s", file_path, e)
        
        # Clean up temp file
        tmp_file = file_path.with_suffix(".tmp")
        if tmp_file.exists():
            try:
                tmp_file.unlink()
            except OSError:
                pass
        
        return False


def safe_read_json(file_path: Path, default: Optional[dict] = None) -> tuple:
    """
    Safely read JSON with corruption detection and recovery.
    
    Returns (data, was_recovered)
    - was_recovered=True means we fell back to backup
    """
    file_path = Path(file_path)
    default = default if default is not None else {}
    
    # Try primary file
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wrapped = json.load(f)
            
            data, is_valid = unwrap_with_verification(wrapped)
            
            if is_valid:
                return data, False
            else:
                logger.warning("Checksum mismatch in %s - attempting recovery", file_path)
        
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to read %s: %s - attempting recovery", file_path, e)
    
    # Try backup file
    backup_file = file_path.with_suffix(".json.bak")
    if backup_file.exists():
        try:
            with open(backup_file, "r", encoding="utf-8") as f:
                wrapped = json.load(f)
            
            data, _ = unwrap_with_verification(wrapped)
            
            # Restore from backup
            shutil.copy2(backup_file, file_path)
            logger.info("Recovered %s from backup", file_path)
            
            return data, True
        
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Backup recovery failed for %s: %s", file_path, e)
    
    # Return default
    logger.warning("No valid data found for %s, using default", file_path)
    return default, False


# ============================================================
# POSITION STORAGE
# ============================================================

class PositionStorage:
    """Thread-safe position storage with atomic writes."""
    
    def __init__(self):
        self._positions: Dict[str, dict] = {}
        self._loaded = False
    
    def _ensure_loaded(self) -> None:
        """Lazy load positions from disk."""
        if not self._loaded:
            with storage_lock():
                if not self._loaded:
                    data, recovered = safe_read_json(POSITIONS_FILE, {"positions": {}})
                    self._positions = data.get("positions", {})
                    self._loaded = True
                    if recovered:
                        logger.warning("Positions recovered from backup")
    
    def get_all(self) -> Dict[str, dict]:
        """Get all positions (thread-safe)."""
        self._ensure_loaded()
        with storage_lock():
            return dict(self._positions)
    
    def get(self, key: str) -> Optional[dict]:
        """Get a specific position by key."""
        self._ensure_loaded()
        with storage_lock():
            return self._positions.get(key)
    
    def exists(self, key: str) -> bool:
        """Check if position exists."""
        self._ensure_loaded()
        with storage_lock():
            return key in self._positions
    
    def add(self, key: str, position: dict) -> bool:
        """Add a new position (thread-safe, atomic write)."""
        self._ensure_loaded()
        with storage_lock():
            if key in self._positions:
                logger.warning("Position %s already exists", key)
                return False
            
            self._positions[key] = position
            
            if not atomic_write_json(POSITIONS_FILE, {"positions": self._positions}):
                # Rollback
                del self._positions[key]
                return False
            
            return True
    
    def update(self, key: str, updates: dict) -> bool:
        """Update an existing position."""
        self._ensure_loaded()
        with storage_lock():
            if key not in self._positions:
                return False
            
            old_position = self._positions[key].copy()
            self._positions[key].update(updates)
            
            if not atomic_write_json(POSITIONS_FILE, {"positions": self._positions}):
                # Rollback
                self._positions[key] = old_position
                return False
            
            return True
    
    def remove(self, key: str) -> Optional[dict]:
        """Remove a position and return it."""
        self._ensure_loaded()
        with storage_lock():
            if key not in self._positions:
                return None
            
            position = self._positions.pop(key)
            
            if not atomic_write_json(POSITIONS_FILE, {"positions": self._positions}):
                # Rollback
                self._positions[key] = position
                return None
            
            return position
    
    def count(self) -> int:
        """Get position count."""
        self._ensure_loaded()
        with storage_lock():
            return len(self._positions)


# ============================================================
# TRADE HISTORY STORAGE
# ============================================================

class TradeHistoryStorage:
    """Thread-safe trade history storage with size limits."""
    
    def __init__(self):
        self._trades: List[dict] = []
        self._loaded = False
    
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            with analytics_lock():
                if not self._loaded:
                    data, _ = safe_read_json(TRADE_HISTORY_FILE, {"trades": []})
                    self._trades = data.get("trades", [])[-MAX_TRADE_HISTORY:]
                    self._loaded = True
    
    def get_all(self, limit: int = 100) -> List[dict]:
        """Get trade history (most recent first)."""
        self._ensure_loaded()
        with analytics_lock():
            return list(reversed(self._trades[-limit:]))
    
    def add(self, trade: dict) -> bool:
        """Add a trade to history."""
        self._ensure_loaded()
        with analytics_lock():
            # Enforce size limit
            if len(self._trades) >= MAX_TRADE_HISTORY:
                self._trades = self._trades[-(MAX_TRADE_HISTORY - 1):]
            
            trade["recorded_at"] = datetime.now(timezone.utc).isoformat()
            self._trades.append(trade)
            
            return atomic_write_json(
                TRADE_HISTORY_FILE,
                {"trades": self._trades},
                create_backup=len(self._trades) % 100 == 0  # Backup every 100 trades
            )
    
    def get_stats(self) -> dict:
        """Get trade statistics."""
        self._ensure_loaded()
        with analytics_lock():
            if not self._trades:
                return {"total": 0, "wins": 0, "losses": 0, "total_pnl": 0}
            
            wins = sum(1 for t in self._trades if t.get("pnl", 0) > 0)
            losses = sum(1 for t in self._trades if t.get("pnl", 0) < 0)
            total_pnl = sum(t.get("pnl", 0) for t in self._trades)
            
            return {
                "total": len(self._trades),
                "wins": wins,
                "losses": losses,
                "win_rate": round(wins / len(self._trades) * 100, 1) if self._trades else 0,
                "total_pnl": round(total_pnl, 2)
            }


# ============================================================
# ANALYTICS STORAGE
# ============================================================

class AnalyticsStorage:
    """Thread-safe analytics storage."""
    
    def __init__(self):
        self._data: dict = {}
        self._loaded = False
    
    def _ensure_loaded(self) -> None:
        if not self._loaded:
            with analytics_lock():
                if not self._loaded:
                    self._data, _ = safe_read_json(ANALYTICS_FILE, {})
                    self._loaded = True
    
    def get(self, key: str, default: Any = None) -> Any:
        """Get analytics value."""
        self._ensure_loaded()
        with analytics_lock():
            return self._data.get(key, default)
    
    def set(self, key: str, value: Any) -> bool:
        """Set analytics value."""
        self._ensure_loaded()
        with analytics_lock():
            self._data[key] = value
            return atomic_write_json(ANALYTICS_FILE, self._data)
    
    def update(self, updates: dict) -> bool:
        """Update multiple analytics values."""
        self._ensure_loaded()
        with analytics_lock():
            self._data.update(updates)
            return atomic_write_json(ANALYTICS_FILE, self._data)
    
    def get_all(self) -> dict:
        """Get all analytics data."""
        self._ensure_loaded()
        with analytics_lock():
            return dict(self._data)


# ============================================================
# BACKUP MANAGEMENT
# ============================================================

def create_backup(tag: str = "") -> str:
    """
    Create a full backup of all storage files.
    Returns backup folder name.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_name = f"backup_{timestamp}_{tag}" if tag else f"backup_{timestamp}"
    backup_path = BACKUP_DIR / backup_name
    backup_path.mkdir(parents=True, exist_ok=True)
    
    files_to_backup = [
        POSITIONS_FILE,
        TRADE_HISTORY_FILE,
        ANALYTICS_FILE,
        BOT_STATE_FILE
    ]
    
    for file_path in files_to_backup:
        if file_path.exists():
            shutil.copy2(file_path, backup_path / file_path.name)
    
    logger.info("Created backup: %s", backup_name)
    
    # Cleanup old backups
    cleanup_old_backups()
    
    return backup_name


def cleanup_old_backups() -> int:
    """Remove old backups exceeding MAX_BACKUPS."""
    if not BACKUP_DIR.exists():
        return 0
    
    backups = sorted(BACKUP_DIR.iterdir(), key=lambda p: p.stat().st_mtime)
    
    removed = 0
    while len(backups) > MAX_BACKUPS:
        oldest = backups.pop(0)
        try:
            shutil.rmtree(oldest)
            removed += 1
        except OSError as e:
            logger.warning("Failed to remove backup %s: %s", oldest, e)
    
    return removed


def restore_backup(backup_name: str) -> bool:
    """Restore from a specific backup."""
    backup_path = BACKUP_DIR / backup_name
    
    if not backup_path.exists():
        logger.error("Backup not found: %s", backup_name)
        return False
    
    # Create safety backup before restore
    create_backup("pre_restore")
    
    for file_path in backup_path.iterdir():
        if file_path.suffix == ".json":
            dest = DATA_DIR / file_path.name
            shutil.copy2(file_path, dest)
    
    logger.info("Restored from backup: %s", backup_name)
    return True


def list_backups() -> List[dict]:
    """List available backups."""
    if not BACKUP_DIR.exists():
        return []
    
    backups = []
    for path in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if path.is_dir():
            backups.append({
                "name": path.name,
                "timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                "size_kb": sum(f.stat().st_size for f in path.iterdir()) / 1024
            })
    
    return backups


# ============================================================
# CORRUPTION DETECTION
# ============================================================

def check_storage_integrity() -> dict:
    """Check integrity of all storage files."""
    results = {
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "files": {},
        "overall_status": "healthy"
    }
    
    files_to_check = [
        ("positions", POSITIONS_FILE),
        ("trade_history", TRADE_HISTORY_FILE),
        ("analytics", ANALYTICS_FILE),
        ("bot_state", BOT_STATE_FILE)
    ]
    
    for name, file_path in files_to_check:
        if not file_path.exists():
            results["files"][name] = {"status": "missing", "valid": False}
            continue
        
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                wrapped = json.load(f)
            
            _, is_valid = unwrap_with_verification(wrapped)
            
            results["files"][name] = {
                "status": "valid" if is_valid else "checksum_mismatch",
                "valid": is_valid,
                "size_kb": round(file_path.stat().st_size / 1024, 2)
            }
            
            if not is_valid:
                results["overall_status"] = "corrupted"
        
        except json.JSONDecodeError:
            results["files"][name] = {"status": "invalid_json", "valid": False}
            results["overall_status"] = "corrupted"
        except OSError as e:
            results["files"][name] = {"status": f"error: {e}", "valid": False}
            results["overall_status"] = "error"
    
    return results


# ============================================================
# GLOBAL INSTANCES
# ============================================================

# Global storage instances (lazy initialization)
_positions: Optional[PositionStorage] = None
_trade_history: Optional[TradeHistoryStorage] = None
_analytics: Optional[AnalyticsStorage] = None


def get_positions() -> PositionStorage:
    """Get position storage instance."""
    global _positions
    if _positions is None:
        _positions = PositionStorage()
    return _positions


def get_trade_history() -> TradeHistoryStorage:
    """Get trade history storage instance."""
    global _trade_history
    if _trade_history is None:
        _trade_history = TradeHistoryStorage()
    return _trade_history


def get_analytics() -> AnalyticsStorage:
    """Get analytics storage instance."""
    global _analytics
    if _analytics is None:
        _analytics = AnalyticsStorage()
    return _analytics


def get_storage_stats() -> StorageStats:
    """Get overall storage statistics."""
    positions = get_positions()
    trade_history = get_trade_history()
    backups = list_backups()
    
    total_size = 0
    for file_path in [POSITIONS_FILE, TRADE_HISTORY_FILE, ANALYTICS_FILE, BOT_STATE_FILE]:
        if file_path.exists():
            total_size += file_path.stat().st_size
    
    return StorageStats(
        positions_count=positions.count(),
        trade_history_count=len(trade_history.get_all(MAX_TRADE_HISTORY)),
        total_file_size_kb=round(total_size / 1024, 2),
        last_backup=backups[0]["name"] if backups else None,
        corruption_detected=check_storage_integrity()["overall_status"] != "healthy",
        last_integrity_check=datetime.now(timezone.utc).isoformat()
    )


# ============================================================
# INITIALIZATION
# ============================================================

def ensure_storage_initialized() -> None:
    """Ensure storage directories and files exist."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    
    # Initialize files if they don't exist
    if not POSITIONS_FILE.exists():
        atomic_write_json(POSITIONS_FILE, {"positions": {}}, create_backup=False)
    
    if not TRADE_HISTORY_FILE.exists():
        atomic_write_json(TRADE_HISTORY_FILE, {"trades": []}, create_backup=False)
    
    if not ANALYTICS_FILE.exists():
        atomic_write_json(ANALYTICS_FILE, {}, create_backup=False)
    
    logger.info("Storage initialized at %s", DATA_DIR)


# Initialize on import
ensure_storage_initialized()
