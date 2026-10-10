"""
PROJECT-ALPHA CapitalGuard — deterministic capital limit and max-position gating.
"""

from __future__ import annotations

import time

from core.config import AppConfig
from core.logging import get_logger
from core.types import BotName, RiskDecision
from execution.trading.precision_rules import extract_base_coin
from execution.risk.correlation_limits import CorrelationExposureGuard

logger = get_logger("execution.risk.capital_guard")


class CapitalGuard:
    """Enforces per-bot and cross-bot capital limits and maximum open position caps."""

    def __init__(
        self,
        config: AppConfig,
        correlation_guard: CorrelationExposureGuard | None = None,
    ) -> None:
        self._config = config
        self._correlation_guard = correlation_guard

    def check_trade(
        self,
        bot: BotName,
        requested_amount: float,
        current_bot_deployed: float,
        total_deployed: float,
        current_bot_positions: int,
        active_positions: list | None = None,
        current_coin: str | None = None,
        cooldowns: dict[str, dict] | None = None,
    ) -> RiskDecision:
        t0 = time.perf_counter()

        # -1. Minimum Order Sizing Gate (Mandatory >= ₹200.00 in all execution modes)
        # Reject non-finite values as well as values below the project invariant.
        if (
            not isinstance(requested_amount, (int, float))
            or not (requested_amount == requested_amount)
            or requested_amount < 200.0
        ):
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_MIN_ORDER_SIZE",
                reason=f"Requested order amount ₹{requested_amount:.2f} is below mandatory minimum order size of ₹200.00.",
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        candidate_base = extract_base_coin(current_coin)

        # 0. Post-Exit Cooldown Check (Defense-in-Depth)
        if candidate_base and cooldowns:
            if candidate_base in cooldowns:
                ms = (time.perf_counter() - t0) * 1000.0
                c_info = cooldowns[candidate_base]
                return RiskDecision(
                    allowed=False,
                    code="OPPORTUNITY_IN_COOLDOWN",
                    reason=f"Asset {candidate_base} is in post-exit cooldown following {c_info.get('exit_reason', 'EXIT')}.",
                    bot=bot,
                    amount=requested_amount,
                    adjusted_amount=0.0,
                    check_ms=round(ms, 2),
                )

        # 1. Single-Coin Asset Deduplication & Cross-Strategy Fleet Lock Check
        # If one coin position is active in ANY strategy bot, do not allow opening in any strategy bot.
        if (
            self._config.enforce_single_coin_lock
            and candidate_base
            and active_positions
        ):
            for pos in active_positions:
                pos_coin = (
                    getattr(pos, "coin", None)
                    or (pos.get("coin") if isinstance(pos, dict) else "")
                    or ""
                )
                pos_pair = (
                    getattr(pos, "pair", None)
                    or (pos.get("pair") if isinstance(pos, dict) else "")
                    or ""
                )
                pos_bot = getattr(pos, "bot", None) or (
                    pos.get("bot") if isinstance(pos, dict) else "BOT"
                )
                pos_bot_name = (
                    pos_bot.value if hasattr(pos_bot, "value") else str(pos_bot)
                )

                pos_base = extract_base_coin(pos_coin) or extract_base_coin(pos_pair)
                if candidate_base == pos_base:
                    ms = (time.perf_counter() - t0) * 1000.0
                    target_bot_name = bot.value if hasattr(bot, "value") else str(bot)
                    if pos_bot_name.upper() != target_bot_name.upper():
                        reason_str = (
                            f"Asset {candidate_base} already has an active open position in strategy {pos_bot_name}. "
                            f"Cross-strategy lock prevents opening in {target_bot_name}."
                        )
                    else:
                        reason_str = f"Asset {candidate_base} already has an active open position in strategy {pos_bot_name}."
                    return RiskDecision(
                        allowed=False,
                        code="OPPORTUNITY_LOCKED_ACTIVE_PAIR",
                        reason=reason_str,
                        bot=bot,
                        amount=requested_amount,
                        adjusted_amount=0.0,
                        check_ms=round(ms, 2),
                    )

        # 1.5. Sector / Correlation Exposure Limits Check
        if self._correlation_guard and current_coin:
            corr_dec = self._correlation_guard.check_exposure(
                bot=bot,
                candidate_coin=current_coin,
                requested_amount=requested_amount,
                active_positions=active_positions,
            )
            if not corr_dec.allowed:
                return corr_dec

        # 2. Max Fleet-Wide Concurrency Cap Check
        if (
            active_positions is not None
            and len(active_positions) >= self._config.max_concurrent_positions
        ):
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_MAX_FLEET_POSITIONS",
                reason=f"Max fleet-wide concurrent positions reached ({len(active_positions)}/{self._config.max_concurrent_positions}).",
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        # 3. Check Max Open Positions (Per Strategy)
        max_pos = self._get_max_positions(bot)
        if current_bot_positions >= max_pos:
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_MAX_POSITIONS",
                reason=f"Max position limit reached for {bot.value} ({current_bot_positions}/{max_pos}).",
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        # 4. Check Per-Bot Capital Limit (if discrete allocation configured)
        bot_limit = self._get_bot_capital_limit(bot)
        if bot_limit > 0 and (current_bot_deployed + requested_amount) > bot_limit:
            available = max(0.0, bot_limit - current_bot_deployed)
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_BOT_CAPITAL",
                reason=f"{bot.value} capital limit exceeded: requested ₹{requested_amount:.2f}, available ₹{available:.2f} (limit ₹{bot_limit:.2f}).",
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        # 5. Check Unified Global Capital Pool Limit (Shared Ceiling, None = dynamic)
        total_limit = self._config.total_capital_limit
        if (
            total_limit is not None
            and total_limit > 0
            and (total_deployed + requested_amount) > total_limit
        ):
            available = max(0.0, total_limit - total_deployed)
            ms = (time.perf_counter() - t0) * 1000.0
            return RiskDecision(
                allowed=False,
                code="BLOCKED_TOTAL_CAPITAL",
                reason=f"Total portfolio capital limit exceeded: requested ₹{requested_amount:.2f}, available ₹{available:.2f} (limit ₹{total_limit:.2f}).",
                bot=bot,
                amount=requested_amount,
                adjusted_amount=0.0,
                check_ms=round(ms, 2),
            )

        ms = (time.perf_counter() - t0) * 1000.0
        return RiskDecision(
            allowed=True,
            code="ALLOWED",
            reason=f"Capital checks passed for {bot.value}.",
            bot=bot,
            amount=requested_amount,
            adjusted_amount=requested_amount,
            check_ms=round(ms, 2),
        )

    def _get_bot_capital_limit(self, bot: BotName) -> float:
        if bot == BotName.STE:
            return self._config.ste_capital_limit
        if bot == BotName.HDA:
            return self._config.hda_capital_limit
        if bot == BotName.VCP:
            return self._config.vcp_capital_limit
        if bot == BotName.BBS:
            return self._config.bbs_capital_limit
        if bot == BotName.MTB:
            return self._config.mtb_capital_limit
        if bot == BotName.NR7:
            return self._config.nr7_capital_limit
        if bot == BotName.PPA:
            return self._config.ppa_capital_limit
        if bot == BotName.MRB:
            return self._config.mrb_capital_limit
        return 0.0

    def _get_max_positions(self, bot: BotName) -> int:
        if bot == BotName.STE:
            return self._config.max_positions_ste
        if bot == BotName.HDA:
            return self._config.max_positions_hda
        if bot == BotName.VCP:
            return self._config.max_positions_vcp
        if bot == BotName.BBS:
            return self._config.max_positions_bbs
        if bot == BotName.MTB:
            return self._config.max_positions_mtb
        if bot == BotName.NR7:
            return self._config.max_positions_nr7
        if bot == BotName.PPA:
            return self._config.max_positions_ppa
        if bot == BotName.MRB:
            return self._config.max_positions_mrb
        return 5
