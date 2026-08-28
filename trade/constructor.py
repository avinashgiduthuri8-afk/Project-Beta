"""Trade Construction Engine for Indian Equities & Derivatives (Project-Beta)."""

from __future__ import annotations

import logging
import uuid
from typing import Optional
from core.models import ScannerCandidate, AISignalEvaluation, TradePlan, AccountBalance
from core.enums import OrderSide, ProductType, Exchange
from risk.position_sizer import PositionSizer
from oms.execution_router import ExecutionRouter

logger = logging.getLogger(__name__)


class TradeConstructor:
    """Constructs disciplined trade plans with minimum 1:2 R:R, ATR buffers, and lot quantization."""

    @staticmethod
    def construct_plan(
        candidate: ScannerCandidate,
        ai_evaluation: AISignalEvaluation,
        account_balance: AccountBalance,
        risk_per_trade_pct: float = 1.0,
        min_rr_ratio: float = 2.0,
        lot_size: int = 1,
        product_type: ProductType = ProductType.MIS,
    ) -> Optional[TradePlan]:
        entry_price = ExecutionRouter.normalize_tick_size(candidate.ltp)
        
        # Stop-loss placement: Below invalidation price or Entry - (1.2 * ATR)
        raw_sl = min(ai_evaluation.invalidation_price, entry_price - (1.2 * candidate.atr_14))
        stop_loss = ExecutionRouter.normalize_tick_size(raw_sl)
        risk_per_share = round(entry_price - stop_loss, 2)

        if risk_per_share <= 0:
            logger.warning(f"[TradeConstructor] Invalid stop-loss ₹{stop_loss} >= Entry ₹{entry_price} for {candidate.symbol}")
            return None

        # Profit Target based on AI recommended R:R ratio
        target_rr = max(min_rr_ratio, ai_evaluation.recommended_rr_ratio)
        target_price = ExecutionRouter.normalize_tick_size(entry_price + (risk_per_share * target_rr))
        reward_per_share = round(target_price - entry_price, 2)
        realized_rr = round(reward_per_share / risk_per_share, 2)

        if realized_rr < min_rr_ratio:
            logger.warning(f"[TradeConstructor] Trade rejected: Realized R:R {realized_rr} < Minimum {min_rr_ratio}")
            return None

        # Calculate position size
        quantity = PositionSizer.calculate_quantity(
            capital=account_balance.available_margin,
            risk_per_trade_pct=risk_per_trade_pct,
            entry_price=entry_price,
            stop_loss_price=stop_loss,
            lot_size=lot_size,
        )

        if quantity <= 0:
            logger.warning(f"[TradeConstructor] Calculated quantity is 0 for {candidate.symbol}. Insufficient capital/risk margin.")
            return None

        total_capital = round(entry_price * quantity * (0.20 if product_type == ProductType.MIS else 1.0), 2)

        plan = TradePlan(
            plan_id=f"PLAN-{uuid.uuid4().hex[:6].upper()}",
            symbol=candidate.symbol,
            exchange=candidate.exchange,
            side=OrderSide.BUY,
            product_type=product_type,
            setup_type=candidate.setup_type,
            entry_price=entry_price,
            stop_loss=stop_loss,
            target_price=target_price,
            risk_per_share=risk_per_share,
            reward_per_share=reward_per_share,
            risk_reward_ratio=realized_rr,
            calculated_quantity=quantity,
            total_capital_required=total_capital,
            ai_confidence=ai_evaluation.confidence_score,
        )

        logger.info(
            f"[TradeConstructor] 📐 Constructed Plan for {candidate.symbol}: "
            f"Entry=₹{entry_price:.2f}, SL=₹{stop_loss:.2f}, Target=₹{target_price:.2f}, "
            f"Qty={quantity}, R:R=1:{realized_rr}"
        )
        return plan
