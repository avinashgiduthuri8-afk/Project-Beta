"""
V2 Notification Formatters — produces structured HTML/emoji messages for Telegram and logs.
"""

from __future__ import annotations

import re
from typing import Any


def format_qty(qty: float | None) -> str:
    """Format quantity dynamically preserving micro-lots without trailing zeros."""
    if qty is None:
        return "0"
    try:
        q = float(qty)
    except (ValueError, TypeError):
        return str(qty)
    if q == 0.0:
        return "0"
    s = f"{q:.6f}".rstrip("0").rstrip(".")
    return s if s else "0"

def fmt_price(p: float) -> str:
    """Format prices dynamically for sub-satoshi precision."""
    if p >= 1.0:
        return f"{p:.2f}"
    elif p >= 0.0001:
        return f"{p:.6f}"
    else:
        return f"{p:.10f}"

def format_signal_ai_alert(payload: dict[str, Any]) -> str:
    """Format AI Intelligence confirmation or rejection alert."""
    coin = payload.get("coin", "UNKNOWN")
    rec = payload.get("recommendation", "WATCH")
    conf = payload.get("confidence_score", 0)
    trend = payload.get("trend_evaluation", "N/A")
    setup = payload.get("setup_quality", "N/A")
    factors = payload.get("supporting_factors") or []
    risks = payload.get("risk_factors") or []

    emoji = "🟢" if rec == "APPROVE" else "🟡" if rec == "SCALE_DOWN" else "🔴"

    lines = [
        f"{emoji} <b>AI Intelligence — {coin}</b>",
        f"<b>Recommendation:</b> {rec} (Confidence: <code>{conf}%</code>)",
        f"<b>Trend:</b> {trend}",
        f"<b>Setup:</b> {setup}",
    ]

    if factors:
        lines.append(f"<b>Key Strengths:</b> {', '.join(factors[:2])}")
    if risks:
        lines.append(f"<b>Risk Factors:</b> {', '.join(risks[:2])}")

    return "\n".join(lines)


def format_trade_approved_alert(payload: dict[str, Any]) -> str:
    """Format risk-approved trade alert."""
    coin = payload.get("coin", "UNKNOWN")
    bot = payload.get("bot", "MTB")
    amount = float(payload.get("approved_amount", 0.0))
    adjustments = payload.get("ai_adjustments") or {}
    multiplier = adjustments.get("size_multiplier", 1.0)

    return (
        f"⚡ <b>Trade Approved — {coin}</b>\n"
        f"<b>Bot Strategy:</b> <code>{bot}</code>\n"
        f"<b>Allocated Capital:</b> ₹{amount:.2f} (AI Scale: <code>{multiplier}x</code>)"
    )


def format_trade_denied_alert(payload: dict[str, Any]) -> str:
    """Format risk-denied trade alert."""
    coin = payload.get("coin", "UNKNOWN")
    bot = payload.get("bot", "MTB")
    code = payload.get("code", "BLOCKED")
    reason = payload.get("reason", "Capital limit reached")

    return (
        f"🛡️ <b>Trade Blocked by Risk Engine — {coin}</b>\n"
        f"<b>Bot:</b> <code>{bot}</code>\n"
        f"<b>Code:</b> <code>{code}</code>\n"
        f"<b>Reason:</b> {reason}"
    )


def format_position_opened_alert(payload: dict[str, Any]) -> str:
    """Format position opened execution alert matching C2 specification."""
    coin = payload.get("coin", "UNKNOWN")
    pair = payload.get("pair") or f"{coin}/INR"
    bot = payload.get("bot", "STE")
    price = float(payload.get("entry_price", 0.0))
    qty = float(payload.get("qty", 0.0) or 0.0)

    is_usdt = "USDT" in pair or payload.get("quote") == "USDT"
    px_sym = "$" if is_usdt else "₹"
    usdt_rate = float(payload.get("usdt_inr_rate", 91.50))

    raw_amount = float(payload.get("amount", 0.0) or (qty * price))
    if is_usdt:
        if raw_amount < 50.0:
            usdt_amount = raw_amount
            amount_inr = raw_amount * usdt_rate
        else:
            amount_inr = raw_amount
            usdt_amount = amount_inr / usdt_rate
        amt_str = f"${usdt_amount:.2f} (₹{amount_inr:.2f})"
    else:
        amt_str = f"₹{raw_amount:.2f}"

    sl = payload.get("stop_loss")
    tp = payload.get("take_profit")

    sl_str = f"{px_sym}{fmt_price(float(sl))}" if sl is not None else "None"
    tp_str = f"{px_sym}{fmt_price(float(tp))}" if tp is not None else "None"

    return (
        f"🟢 <b>BUY EXECUTED</b>\n"
        f"<b>Coin:</b> <code>{coin}</code> | <b>Bot:</b> <code>{bot}</code>\n"
        f"<b>Amount:</b> {amt_str} | <b>Entry:</b> {px_sym}{fmt_price(price)}\n"
        f"<b>TP:</b> {tp_str} | <b>SL:</b> {sl_str}"
    )


def format_position_closed_alert(payload: dict[str, Any]) -> str:
    """Format position closed / trade exit alert."""
    coin = payload.get("coin", "UNKNOWN")
    pair = payload.get("pair") or f"{coin}/INR"
    bot = payload.get("bot", "MTB")
    pnl = float(payload.get("pnl", 0.0))
    pnl_pct = float(payload.get("pnl_pct", 0.0))
    reason = payload.get("exit_reason", "MANUAL")
    price = float(payload.get("exit_price", 0.0))

    is_usdt = "USDT" in pair or "USD" in pair or payload.get("quote") in ("USDT", "USD")
    px_sym = "$" if is_usdt else "₹"

    emoji = "💰" if pnl >= 0 else "🛑"
    sign = "+" if pnl >= 0 else ""

    if reason == "STOP_LOSS" and pnl > 0:
        display_reason = "TRAILING_STOP (Profit Lock)"
    elif reason == "STOP_LOSS":
        display_reason = "STOP_LOSS"
    else:
        display_reason = reason

    if is_usdt:
        fx = float(payload.get("fx_rate", 91.50))
        pnl_inr = pnl * fx
        pnl_str = f"{sign}${pnl:.2f} (₹{sign}{pnl_inr:,.2f}) ({sign}{pnl_pct:.2f}%)"
    else:
        pnl_str = f"{sign}₹{pnl:,.2f} ({sign}{pnl_pct:.2f}%)"

    return (
        f"{emoji} <b>Position Closed — {coin}</b>\n"
        f"<b>Bot:</b> <code>{bot}</code> | <b>Exit Price:</b> {px_sym}{fmt_price(price)}\n"
        f"<b>Realized PnL:</b> <code>{pnl_str}</code>\n"
        f"<b>Exit Trigger:</b> <code>{display_reason}</code>"
    )


def format_circuit_breaker_alert(payload: dict[str, Any]) -> str:
    """Format emergency circuit breaker alert."""
    reason = payload.get("reason", "Threshold breached")
    return (
        f"🚨 <b>CIRCUIT BREAKER TRIGGERED</b> 🚨\n"
        f"<b>Reason:</b> {reason}\n"
        f"All automated trade entries have been suspended."
    )


def format_divergence_alert(payload: dict[str, Any]) -> str:
    """Format shadow mode alpha divergence alert."""
    coin = payload.get("coin", "UNKNOWN")
    bot = payload.get("bot", "MTB")
    div_type = payload.get("divergence_type", "AI_FILTERED")
    reason = payload.get("reason", "N/A")

    return (
        f"🧠 <b>Decision Divergence Detected — {coin}</b>\n"
        f"<b>Bot:</b> <code>{bot}</code> | <b>Type:</b> <code>{div_type}</code>\n"
        f"<b>Reason:</b> {reason}"
    )


def format_generic_alert(payload: dict[str, Any]) -> str:
    """Format generic system alert."""
    level = payload.get("level", "INFO").upper()
    title = payload.get("title", "System Notification")
    message = payload.get("message", "")

    emoji = "ℹ️" if level == "INFO" else "⚠️" if level == "WARNING" else "🚨"

    return f"{emoji} <b>{title}</b>\n{message}"


# ── Interactive Telegram C2 Interface Formatters ──────────────────────────────


def format_telegram_menu(overview: dict[str, Any]) -> str:
    """Format the primary Mission Control interactive hub menu."""
    status_str = overview.get("status", "HEALTHY")
    total_aum = overview.get("total_aum", 0.0)
    deployed = overview.get("total_deployed", 0.0)
    daily_pnl = overview.get("daily_pnl", 0.0)
    active_positions = overview.get("active_positions", 0)
    bot_count = overview.get("bot_count", 4)
    trading_mode = overview.get("trading_mode", "PAPER ACTIVE")

    sign = "+" if daily_pnl >= 0 else ""
    pnl_emoji = "🟢" if daily_pnl >= 0 else "🔴"

    return (
        f"🚀 <b>PROJECT-ALPHA · Mission Control C2</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Status:</b> <code>{status_str}</code> | <b>Mode:</b> <code>{trading_mode}</code>\n"
        f"<b>AUM:</b> ₹{total_aum:,.2f} | <b>Deployed:</b> ₹{deployed:,.2f}\n"
        f"<b>Daily PnL:</b> {pnl_emoji} <code>{sign}₹{daily_pnl:,.2f}</code>\n"
        f"<b>Open Positions:</b> <code>{active_positions}</code> | <b>Bots:</b> <code>{bot_count} Production</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Tap any button below to inspect subsystems or manage trading:</i>"
    )


def format_telegram_bot_fleet(fleet: dict[str, Any] | list[dict[str, Any]]) -> str:
    """Format the bot fleet status card matching C2 specification."""
    if isinstance(fleet, dict):
        open_pos = fleet.get("open_positions", 0)
        today_trades = fleet.get("today_trades", 0)
        daily_pnl = float(fleet.get("daily_pnl", 0.0))
        signals_today = fleet.get("signals_today", 0)
        bot_statuses = fleet.get("bot_statuses", {})
    else:
        open_pos = sum(b.get("open_positions", 0) for b in fleet)
        today_trades = sum(b.get("today_trades", 0) for b in fleet)
        daily_pnl = sum(float(b.get("daily_pnl", 0.0)) for b in fleet)
        signals_today = sum(b.get("signals_today", 0) for b in fleet)
        bot_statuses = {b.get("name", "BOT"): b.get("status", "ACTIVE") for b in fleet}

    ste_status = bot_statuses.get("STE", "ACTIVE")
    hda_status = bot_statuses.get("HDA", "ACTIVE")
    vcp_status = bot_statuses.get("VCP", "ACTIVE")
    bbs_status = bot_statuses.get("BBS", "ACTIVE")
    mtb_status = bot_statuses.get("MTB", "ACTIVE")
    nr7_status = bot_statuses.get("NR7", "ACTIVE")
    ppa_status = bot_statuses.get("PPA", "ACTIVE")
    mrb_status = bot_statuses.get("MRB", "ACTIVE")

    def _icon(s: str) -> str:
        return (
            "🟢 ACTIVE"
            if s.upper() in ("ACTIVE", "HEALTHY", "RUNNING", "IDLE")
            else f"🟡 {s.upper()}"
        )

    pnl_sign = "+" if daily_pnl >= 0 else ""

    lines = [
        "🤖 <b>BOT FLEET</b>",
        f"STE   {_icon(ste_status)}",
        f"HDA   {_icon(hda_status)}",
        f"VCP   {_icon(vcp_status)}",
        f"BBS   {_icon(bbs_status)}",
        f"MTB   {_icon(mtb_status)}",
        f"NR7   {_icon(nr7_status)}",
        f"PPA   {_icon(ppa_status)}",
        f"MRB   {_icon(mrb_status)}",
        "",
        f"Open Positions: {open_pos}",
        f"Today's Trades: {today_trades}",
        f"Today's P&L: {pnl_sign}₹{daily_pnl:.2f}",
        f"Signals Today: {signals_today}",
    ]
    return "\n".join(lines)


def format_telegram_pipeline_stages(stages: list[dict[str, Any]]) -> str:
    """Format the 11 closed-loop pipeline stages overview."""
    lines = [
        "📊 <b>11-STAGE AUTONOMOUS PIPELINE</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for s in stages:
        num = s.get("number", 0)
        name = s.get("name", "")
        status = s.get("status", "ACTIVE")
        processed = s.get("processed_count", 0)
        rejected = s.get("rejected_count", 0)
        icon = "🟢" if status == "ACTIVE" else "🟡" if status == "STANDBY" else "⚪"

        line = (
            f"<b>{num:02d}. {name}</b> {icon}\n   └ Processed: <code>{processed}</code>"
        )
        if rejected > 0:
            line += f" | Filtered: <code>{rejected}</code>"
        lines.append(line)

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_positions(positions: list[dict[str, Any]]) -> str:
    """Format list of live open positions."""
    if not positions:
        return (
            "📈 <b>ACTIVE FLEET POSITIONS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No active open positions. Capital is parked safely in cash reserve.</i>"
        )

    lines = [
        f"📈 <b>ACTIVE FLEET POSITIONS ({len(positions)})</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for p in positions:
        coin = p.get("coin", "UNKNOWN")
        pair = p.get("pair") or f"{coin}/INR"
        qty = float(p.get("qty", 0.0) or 0.0)
        entry = float(p.get("entry_price", 0.0) or 0.0)
        raw_cur = p.get("current_price")
        cur = float(raw_cur) if (raw_cur is not None and float(raw_cur) > 0) else None
        status = p.get("status", "OPEN")
        sl = p.get("stop_loss")
        tp = p.get("take_profit")

        is_usdt = "USDT" in pair or p.get("quote") == "USDT"
        sym = "$" if is_usdt else "₹"
        raw_amt = float(p.get("amount", 0.0) or 0.0)
        usdt_rate = float(p.get("usdt_inr_rate", 91.50))
        if is_usdt:
            deployed = (qty * entry) if (qty > 0 and entry > 0) else (raw_amt / usdt_rate if raw_amt > 0 else 0.0)
            cap_str = f"${deployed:.2f} (₹{deployed * usdt_rate:.2f})"
        else:
            deployed = raw_amt if raw_amt > 0 else (qty * entry)
            cap_str = f"₹{deployed:.2f}"

        sl_str = f"{sym}{fmt_price(float(sl))}" if sl is not None else "None"
        tp_str = f"{sym}{fmt_price(float(tp))}" if tp is not None else "None"

        if cur is None:
            lines.append(
                f"⚪ <b>{pair}</b> [{status}]\n"
                f"   • Entry: {sym}{fmt_price(entry)} | Capital: {cap_str}\n"
                f"   • Unrealized P&L: <code>UNAVAILABLE</code>\n"
                f"   • TP: {tp_str} | SL: {sl_str}\n"
                f"   • Status: <code>{status}</code>"
            )
        else:
            unrealized = float(
                p.get("unrealised_pnl", 0.0)
                if p.get("unrealised_pnl") is not None
                else (cur - entry) * qty
            )
            unrealized_pct = (
                (unrealized / deployed * 100.0)
                if deployed > 0
                else (((cur - entry) / entry) * 100.0 if entry > 0 else 0.0)
            )
            sign = "+" if unrealized >= 0 else ""
            pct_sign = "+" if unrealized_pct >= 0 else ""
            emoji = "🟢" if unrealized >= 0 else "🔴"
            lines.append(
                f"{emoji} <b>{pair}</b> [{status}]\n"
                f"   • Entry: {sym}{fmt_price(entry)} | Capital: {cap_str}\n"
                f"   • Unrealized P&L: <code>{sign}{sym}{unrealized:.2f} ({pct_sign}{unrealized_pct:.2f}%)</code>\n"
                f"   • TP: {tp_str} | SL: {sl_str}\n"
                f"   • Status: <code>{status}</code>"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_trades(trades: list[dict[str, Any]]) -> str:
    """Format recent closed trades history."""
    if not trades:
        return (
            "📜 <b>RECENT TRADES HISTORY</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No closed trades recorded in this session.</i>"
        )

    lines = [
        f"📜 <b>RECENT TRADES HISTORY (Last {min(5, len(trades))})</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for t in trades[:5]:
        coin = t.get("coin", "UNKNOWN")
        pair = t.get("pair") or f"{coin}/INR"
        bot = t.get("bot", "STE")
        pnl = float(t.get("pnl", 0.0))
        pct = float(t.get("pnl_pct", 0.0))
        reason = t.get("exit_reason", "EXIT")
        display_reason = "TRAIL_TP" if reason == "STOP_LOSS" and pnl > 0 else reason
        emoji = "💰" if pnl >= 0 else "🛑"
        pnl_sign = "+" if pnl >= 0 else ""
        pct_sign = "+" if pct >= 0 else ""

        is_usdt = "USDT" in pair or t.get("quote") == "USDT"
        sym = "$" if is_usdt else "₹"

        lines.append(
            f"{emoji} <b>{coin}</b> (<code>{bot}</code>) — <b>{display_reason}</b>\n"
            f"   • PnL: <code>{pnl_sign}{sym}{pnl:.2f} ({pct_sign}{pct:.2f}%)</code>\n"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_signals(signals: list[dict[str, Any]]) -> str:
    """Format high-conviction live signals from confluence engine."""
    if not signals:
        return (
            "🎯 <b>HIGH-CONVICTION SIGNALS</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>No pending signals. Confluence engine filtering low-conviction market noise.</i>"
        )

    lines = [
        f"🎯 <b>HIGH-CONVICTION SIGNALS ({len(signals)})</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for s in signals:
        coin = s.get("coin", "UNKNOWN")
        bot = s.get("bot", "STE")
        score = s.get("confluence_score", s.get("score", 0))
        ai_conf = s.get("ai_confidence", s.get("confidence_score", 0))
        risk_verdict = s.get("risk_verdict", "APPROVED")
        lines.append(
            f"• <b>{coin}</b> | <code>{bot}</code> | C2: <code>{score}</code> | AI: <code>{ai_conf}%</code> | Risk: <code>{risk_verdict}</code>"
        )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Use /signal &lt;symbol&gt; for technical breakdown</i>")
    return "\n".join(lines)


def format_telegram_risk(risk_state: dict[str, Any]) -> str:
    """Format risk engine state and circuit breaker metrics."""
    circuit_open = risk_state.get("circuit_breaker_open", False)
    emergency_stop = risk_state.get("emergency_stop", False)
    tot_raw = risk_state.get("total_capital_limit")
    total_cap_str = (
        f"₹{float(tot_raw):,.2f}" if tot_raw is not None else "Dynamic (Unconstrained)"
    )
    deployed = risk_state.get("per_bot_deployed", {})
    open_counts = risk_state.get("per_bot_open_count", {})

    status_icon = (
        "🔴 TRIPPED" if circuit_open or emergency_stop else "🟢 HEALTHY / ACTIVE"
    )

    lines = [
        "🛡️ <b>RISK ENGINE & CIRCUIT BREAKER</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>Status:</b> <code>{status_icon}</code>",
        f"<b>Emergency Stop:</b> <code>{'ON' if emergency_stop else 'OFF'}</code>",
        f"<b>Total Fleet Capital Cap:</b> <code>{total_cap_str}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Per-Bot Capital Allocation:</b>",
    ]
    for bot, dep in deployed.items():
        cnt = open_counts.get(bot, 0)
        lines.append(f"   • <b>{bot}:</b> ₹{float(dep):,.2f} ({cnt} open positions)")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def mask_sensitive_data(text: str) -> str:
    """Mask credentials, tokens, and API secrets from output strings."""
    if not isinstance(text, str):
        text = str(text)
    # Redact common key/secret patterns
    text = re.sub(
        r'((?:api[_-]?)?key["\']?\s*[:=]\s*["\']?)([^"\'\s,}{]+)',
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'((?:api[_-]?)?secret(?:[_-]?(?:key|token))?["\']?\s*[:=]\s*["\']?)([^"\'\s,}{]+)',
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'((?:auth[_-]?)?token["\']?\s*[:=]\s*["\']?)([^"\'\s,}{]+)',
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r'(password["\']?\s*[:=]\s*["\']?)([^"\'\s,}{]+)',
        r"\1***REDACTED***",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"(bot[0-9]{8,12}:[a-zA-Z0-9_-]{35})", r"***REDACTED_TELEGRAM_TOKEN***", text
    )
    return text


def format_telegram_status(d: dict[str, Any]) -> str:
    """Format comprehensive /status operator response."""
    mode = d.get("mode", "PAPER")
    cap_val = d.get("available_capital")
    if cap_val is not None:
        cap_str = f"₹{cap_val:,.2f}"
    elif "LIVE" in mode.upper():
        cap_str = "COINDCX_UNAVAILABLE"
    else:
        cap_str = "CAPITAL UNKNOWN"
    amt_str = f"₹{d.get('order_amount_inr', 200.0):,.2f}"

    lines = [
        "📊 <b>PROJECT-ALPHA SYSTEM STATUS</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>System Status:</b> <code>{d.get('system_status', 'HEALTHY')}</code>",
        f"• <b>Trading Mode:</b> <code>{mode}</code>",
        f"• <b>Scanner Status:</b> <code>{d.get('scanner_status', 'ACTIVE')}</code> (Polls: {d.get('poll_count', 0)})",
        f"• <b>Execution Status:</b> <code>{d.get('execution_status', 'ACTIVE')}</code>",
        f"• <b>Risk Status:</b> <code>{d.get('risk_status', 'HEALTHY')}</code>",
        f"• <b>EventBus Status:</b> <code>{d.get('event_bus_status', 'OPERATIONAL')}</code>",
        f"• <b>Database Status:</b> <code>{d.get('database_status', 'CONNECTED')}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Configured Order Amount:</b> <code>{amt_str}</code>",
        f"• <b>Available Capital:</b> <code>{cap_str}</code>",
        f"• <b>Open Positions:</b> <code>{d.get('open_positions_count', 0)}</code>",
        f"• <b>Last Scanner Cycle:</b> <code>{d.get('last_scan_at', 'N/A')}</code>",
        f"• <b>Last Execution Event:</b> <code>{d.get('last_execution_at', 'N/A')}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    return "\n".join(lines)


def format_telegram_health(h: dict[str, Any]) -> str:
    """Format component-level and fleet-level /health status."""
    mode = str(h.get("mode", "PAPER")).upper()
    components = h.get("components", {})
    fleet = h.get("fleet", {})

    def icon(ok: bool) -> str:
        return "🟢" if ok else "🔴"

    is_live = mode in ("LIVE", "LIVE_MICROCASH")
    service_title = "LIVE EXECUTION SERVICE HEALTH" if is_live else "PAPER EXECUTION SERVICE HEALTH"
    exec_label = "Live Execution" if is_live else "Paper Execution"
    db_label = "Live Database" if is_live else "Paper Database"

    lines = [
        f"🩺 <b>{service_title}</b>",
        f"<b>Mode:</b> <code>{mode}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Scanner Connection      {icon(components.get('scanner_connection', components.get('scanner', False)))}",
        f"Signal Intake           {icon(components.get('signal_intake', True))}",
        f"Risk Engine             {icon(components.get('risk', False))}",
        f"{exec_label:<24}{icon(components.get('execution', False))}",
        f"{db_label:<24}{icon(components.get('database', False))}",
        f"EventBus                {icon(components.get('event_bus', False))}",
        f"CoinDCX Connectivity    {icon(components.get('coindcx', False))}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<b>Overall Status:</b> <code>{h.get('overall', 'UNKNOWN').upper()}</code>",
        f"<b>Checked At:</b> <code>{h.get('checked_at', 'N/A')}</code>",
    ]

    if fleet:
        lines.extend([
            "",
            "📊 <b>PROJECT-ALPHA SERVICE HEALTH</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"Scanner Service         {icon(fleet.get('scanner', False))}",
            f"Paper Execution         {icon(fleet.get('paper', False))}",
            f"Live Execution          {icon(fleet.get('live', False))}",
        ])

    return "\n".join(lines)


def format_telegram_mode(m: dict[str, Any]) -> str:
    """Format /mode response."""
    mode = m.get("mode", "PAPER").upper()
    trading_enabled = m.get("trading_enabled", True)

    if mode in ("LIVE", "LIVE_MICROCASH"):
        badge = "🔴 LIVE TRADING (REAL MONEY)"
        desc = "Real orders are routed to CoinDCX exchange with live funds (₹200.00 micro-orders)."
    else:
        badge = "🟡 PAPER TRADING (SIMULATION ACTIVE)"
        desc = "Real signals execute virtual positions with live prices, SL/TP exits, and 1.572% friction."

    return (
        f"⚙️ <b>MODE: {mode}</b> — {badge}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Active Mode:</b> <code>{mode}</code>\n"
        f"• <b>Trading Status:</b> <code>{'YES (ACTIVE)' if trading_enabled else 'NO (HALTED)'}</code>\n"
        f"• <b>Micro-Order Size:</b> <code>₹200.00</code>\n"
        f"• <b>Description:</b> {desc}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>To change mode, send:</i>\n"
        f"• <code>/mode paper</code> — activate virtual paper trading\n"
        f"• <code>/mode live confirm</code> — activate live real-capital trading"
    )


def format_telegram_uptime(u: dict[str, Any]) -> str:
    """Format /uptime response."""
    mode = u.get("mode", "PAPER")
    return (
        f"⏱️ <b>SYSTEM UPTIME & TELEMETRY</b>\n"
        f"<b>MODE: {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Server Started:</b> <code>{u.get('started_at', 'N/A')}</code>\n"
        f"• <b>Elapsed Uptime:</b> <code>{u.get('uptime_str', 'N/A')}</code>\n"
        f"• <b>Total Scanner Cycles:</b> <code>{u.get('poll_count', 0)}</code>\n"
        f"• <b>Active Tasks:</b> <code>{u.get('tasks_count', 1)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_telegram_scan(s: dict[str, Any]) -> str:
    """Format /scan summary showing latest cycle and strongest signals."""
    mode = s.get("mode", "PAPER")
    signals = s.get("signals", [])

    lines = [
        "📡 <b>LATEST SCANNER CYCLE</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Cycle Completed:</b> <code>{s.get('last_scan_at', 'N/A')}</code>",
        f"• <b>Coins Evaluated:</b> <code>{s.get('evaluated_count', 0)}</code>",
        f"• <b>Candidates Generated:</b> <code>{s.get('candidate_count', 0)}</code>",
        f"• <b>Confluence Passed:</b> <code>{len(signals)}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Strongest Signals:</b>",
    ]
    if not signals:
        lines.append("<i>No high-conviction signals currently active.</i>")
    else:
        for sig in signals[:5]:
            coin = sig.get("coin", "UNKNOWN")
            pair = sig.get("pair") or f"{coin}/INR"
            is_usdt = "USDT" in pair or sig.get("quote") == "USDT"
            sym = "$" if is_usdt else "₹"
            score = sig.get("confluence_score", sig.get("score", 0))
            price = float(sig.get("price", 0.0))
            direction = sig.get("direction", sig.get("action", "BUY"))
            lines.append(
                f"  • <b>{coin}</b> ({direction}) — Score: <code>{score}%</code> @ {sym}{fmt_price(price)}"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_signal_detail(
    c: dict[str, Any], symbol: str, mode: str = "PAPER"
) -> str:
    """Format /signal <symbol> deep-dive inspector with 4-pillar scores & AI conviction."""
    pair = c.get("pair") or symbol.upper()
    is_usdt = "USDT" in pair or c.get("quote") == "USDT"
    px_sym = "$" if is_usdt else "₹"
    price = float(c.get("price", 0.0))
    c2_score = c.get("confluence_score", c.get("score", 0))
    bot = c.get("bot", "STE")
    status = c.get("status", "EVALUATED")
    rsi = float(c.get("rsi", 50.0))
    vol_24h = float(c.get("volume_24h", 0.0))
    vol_ratio = float(c.get("volume_ratio", 1.0))
    ema_trend = c.get("ema_trend", "N/A")

    # 4 Pillar Breakdown
    eval_b = c.get("eval_breakdown", {})
    chart_score = eval_b.get("chart", {}).get("score", c.get("chart_score", 80))
    indicator_score = eval_b.get("indicator", {}).get(
        "score", c.get("indicator_score", int(c2_score))
    )
    sentiment_score = eval_b.get("sentiment", {}).get(
        "score", c.get("sentiment_score", 85)
    )
    news_score = eval_b.get("news", {}).get("score", c.get("news_score", 90))

    # AI Conviction bullets
    ai_rec = c.get("ai_recommendation") or c.get("recommendation", "WATCH")
    ai_conf = c.get("ai_confidence") or c.get("confidence", 0)
    ai_strengths = c.get("ai_strengths") or c.get("supporting_factors") or []
    ai_risks = c.get("ai_risks") or c.get("risk_factors") or []

    lines = [
        f"🔍 <b>SIGNAL INSPECTOR — {pair}</b>",
        f"<b>MODE: {mode}</b> | <b>Bot:</b> <code>{bot}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Price:</b> {px_sym}{fmt_price(price)}",
        f"• <b>C2 Confluence Score:</b> <code>{c2_score}/100</code> (Status: <code>{status}</code>)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "🏛️ <b>4-Pillar Breakdown:</b>",
        f"  • Technical Setup: <code>{indicator_score}/100</code> (EMA: {ema_trend}, RSI: {rsi:.1f})",
        f"  • Chart Structure: <code>{chart_score}/100</code>",
        f"  • Volume & Liquidity: <code>{vol_ratio:.2f}x vol</code> (24h: ₹{vol_24h:,.0f})",
        f"  • Market Regime & News: <code>{sentiment_score}/100</code> (News: <code>{news_score}/100</code>)",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🧠 <b>AI Conviction:</b> <code>{ai_rec}</code> (<code>{ai_conf}%</code>)",
    ]
    if ai_strengths:
        lines.append(
            f"  • <b>Strengths:</b> {', '.join(str(s) for s in ai_strengths[:2])}"
        )
    if ai_risks:
        lines.append(f"  • <b>Risks:</b> {', '.join(str(r) for r in ai_risks[:2])}")

    reasons = c.get("rejection_reasons") or []
    if reasons:
        lines.append(f"• <b>Gate Remarks:</b> <i>{'; '.join(reasons[:2])}</i>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_watchlist(watchlist: list[str], mode: str = "PAPER") -> str:
    """Format /watchlist response."""
    lines = [
        f"📋 <b>ACTIVE SCANNER WATCHLIST ({len(watchlist)})</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not watchlist:
        lines.append("<i>Watchlist is empty.</i>")
    else:
        chunks = [watchlist[i : i + 4] for i in range(0, len(watchlist), 4)]
        for chunk in chunks:
            lines.append("  • " + " | ".join(f"<b>{c}</b>" for c in chunk))
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"<i>Total Monitored Assets: {len(watchlist)}</i>")
    return "\n".join(lines)


def format_telegram_funnel(f: dict[str, Any], mode: str = "PAPER") -> str:
    """Format /funnel conversion metrics."""
    scanned = f.get("scanned", f.get("raw_signals_count", 0))
    liq_rej = f.get("liquidity_rejected", 0)
    mtf_rej = f.get("mtf_rejected", 0)
    exhaust_rej = f.get("exhaustion_rejected", 0)
    other_rej = f.get("other_risk_rejected", 0)
    qualified = f.get("qualified", f.get("confluence_passed_count", 0))
    signals_gen = f.get("signals_generated", f.get("executed_count", 0))

    lines = [
        "🌪️ <b>5-LAYER SCANNER CONVERSION FUNNEL</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>SCANNED:</b> <code>{scanned}</code>",
        f"  ↳ <b>LIQUIDITY REJECTED:</b> <code>{liq_rej}</code> (Turnover < ₹200k / Spread > 1.0%)",
        f"  ↳ <b>MTF REJECTED:</b> <code>{mtf_rej}</code> (15m/1h/1d Mismatch)",
        f"  ↳ <b>EXHAUSTION REJECTED:</b> <code>{exhaust_rej}</code> (RSI > 72 / Vol Decline)",
        f"  ↳ <b>OTHER/RISK REJECTED:</b> <code>{other_rej}</code>",
        f"• <b>QUALIFIED:</b> <code>{qualified}</code>",
        f"• <b>SIGNALS GENERATED:</b> <code>{signals_gen}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<i>Quality Gate: Hard rejection on illiquid, stale, or exhausted breakout signals.</i>",
    ]
    return "\n".join(lines)


def format_telegram_pnl(p: dict[str, Any]) -> str:
    """Format /pnl summary."""
    mode = p.get("mode", "PAPER")
    realized = float(p.get("realized_pnl", 0.0))
    unrealized = float(p.get("unrealized_pnl", 0.0))
    total = realized + unrealized
    trades_cnt = p.get("trades_count", 0)
    win_rate = float(p.get("win_rate_pct", 0.0))

    real_icon = "🟢" if realized >= 0 else "🔴"
    unreal_icon = "🟢" if unrealized >= 0 else "🔴"
    tot_icon = "🟢" if total >= 0 else "🔴"

    return (
        f"💰 <b>PORTFOLIO PROFIT & LOSS</b>\n"
        f"<b>MODE: {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Realized P&L:</b> {real_icon} ₹{realized:,.2f} (post-statutory friction)\n"
        f"• <b>Unrealized P&L:</b> {unreal_icon} ₹{unrealized:,.2f}\n"
        f"• <b>Total P&L:</b> {tot_icon} <b>₹{total:,.2f}</b>\n"
        f"• <b>Total Closed Trades:</b> <code>{trades_cnt}</code>\n"
        f"• <b>Win Rate:</b> <code>{win_rate:.1f}%</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_telegram_orders(orders: list[dict[str, Any]], mode: str = "PAPER") -> str:
    """Format /orders feed."""
    lines = [
        f"📜 <b>RECENT ORDERS LEDGER ({len(orders)})</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not orders:
        lines.append("<i>No recent orders recorded.</i>")
    else:
        for ord_item in orders[:10]:
            coin = ord_item.get("coin", "UNKNOWN")
            pair = ord_item.get("pair") or f"{coin}/INR"
            side = ord_item.get("side", "BUY")
            qty = ord_item.get("qty", 0.0)
            price = float(ord_item.get("price", 0.0))
            ord_mode = ord_item.get("mode", mode)
            status = ord_item.get("status", "FILLED")
            ex_id = ord_item.get("exchange_order_id") or "N/A"

            is_usdt = "USDT" in pair or ord_item.get("quote") == "USDT"
            sym = "$" if is_usdt else "₹"

            lines.append(
                f"• <b>{coin}</b> [{ord_mode}] — <code>{side}</code> {format_qty(qty)} @ {sym}{fmt_price(price)}\n"
                f"   Status: <code>{status}</code> | ID: <code>{ex_id}</code>"
            )

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_capital(c: dict[str, Any]) -> str:
    """Format /capital response strictly obeying capital reality and C2 specification."""
    mode = c.get("mode", "PAPER")
    avail = c.get("available_capital")
    deployed = float(c.get("deployed_capital", 0.0))
    min_order = float(c.get("min_order_size", 200.0))
    order_amt = max(min_order, float(c.get("order_amount_inr", 200.0)))

    if avail is not None:
        avail_str = f"₹{avail:,.2f}"
        headroom = max(0.0, float(avail) - deployed)
        headroom_str = f"₹{headroom:,.2f}"
    else:
        avail_str = (
            "CAPITAL UNKNOWN" if mode == "LIVE_MICROCASH" else "DYNAMIC (UNCONSTRAINED)"
        )
        headroom_str = (
            "CAPITAL UNKNOWN" if mode == "LIVE_MICROCASH" else "DYNAMIC (UNCONSTRAINED)"
        )

    open_pos_count = int(c.get("open_positions_count", 0))
    per_bot = c.get("per_bot_allocation", {})

    lines = [
        "💼 <b>CAPITAL ALLOCATION & BROKER STATUS</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Available Capital:</b> <code>{avail_str}</code>",
        f"• <b>Deployed Capital:</b> <code>₹{deployed:,.2f}</code>",
        f"• <b>Available Headroom:</b> <code>{headroom_str}</code>",
        f"• <b>Min Order Size:</b> <code>₹{min_order:,.2f}</code> (Configured: <code>₹{order_amt:,.2f}</code>)",
        f"• <b>Open Positions:</b> <code>{open_pos_count}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Per-Bot Allocation:</b>",
    ]
    if per_bot and any(v > 0 for v in per_bot.values()):
        for bot, alloc in per_bot.items():
            lines.append(f"  • <b>{bot}:</b> ₹{float(alloc):,.2f}")
    else:
        lines.append("  • <i>Unified Dynamic Fleet Allocation</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"• <b>Capital Source:</b> <code>{c.get('source', 'COINDCX_EXCHANGE' if mode == 'LIVE_MICROCASH' else 'SIMULATION')}</code>"
    )
    return "\n".join(lines)


def format_telegram_config(cfg: dict[str, Any]) -> str:
    """Format /config response."""
    mode = cfg.get("mode", "PAPER")
    order_amt = float(cfg.get("order_amount_inr", 200.0))
    limit = cfg.get("total_capital_limit")
    limit_str = f"₹{limit:,.2f}" if limit is not None else "DYNAMIC"

    return (
        f"⚙️ <b>TRADING CONFIGURATION</b>\n"
        f"<b>MODE: {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Deployment Mode:</b> <code>{mode}</code>\n"
        f"• <b>Trading Enabled:</b> <code>{'YES' if cfg.get('trading_enabled') else 'NO'}</code>\n"
        f"• <b>Configured Order Amount:</b> <code>₹{order_amt:,.2f}</code>\n"
        f"• <b>Capital Budget Limit:</b> <code>{limit_str}</code>\n"
        f"• <b>Max Concurrent Positions:</b> <code>{cfg.get('max_concurrent_positions', 10)}</code>\n"
        f"• <b>Single Coin Lock:</b> <code>{'ENABLED' if cfg.get('enforce_single_coin_lock') else 'DISABLED'}</code>\n"
        f"• <b>AI Model:</b> <code>{cfg.get('ai_model', 'gemini-2.5-flash')}</code>\n"
        f"• <b>Scanner Poll Interval:</b> <code>{cfg.get('scanner_poll_interval', 60)}s</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


def format_telegram_reconciliation(r: dict[str, Any], mode: str = "PAPER") -> str:
    """Format /reconcile report."""
    status = r.get("status", "IN_SYNC")
    icon = "🟢" if status == "IN_SYNC" else "⚠️"
    discs = r.get("discrepancies", [])

    lines = [
        "🔄 <b>EXCHANGE ORDER RECONCILIATION</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Reconciliation Status:</b> {icon} <code>{status}</code>",
        f"• <b>Local Positions Checked:</b> <code>{r.get('positions_checked', 0)}</code>",
        f"• <b>Exchange Orders Checked:</b> <code>{r.get('orders_checked', 0)}</code>",
        f"• <b>Mismatches Detected:</b> <code>{r.get('mismatches', 0)}</code>",
        f"• <b>Balance Drift:</b> <code>₹{float(r.get('balance_diff', 0.0)):,.2f}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not discs:
        lines.append(
            "<i>Zero discrepancy detected between local ledger and exchange.</i>"
        )
    else:
        lines.append("<b>Discrepancies:</b>")
        for d in discs[:3]:
            lines.append(
                f"  • {d.get('coin', 'ASSET')}: {d.get('exchange_status', 'UNKNOWN')} — {d.get('action', 'FLAGGED')}"
            )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_limits(l: dict[str, Any], mode: str = "PAPER") -> str:
    """Format /limits response."""
    return (
        f"🛡️ <b>RISK ENGINE CONFIGURED LIMITS</b>\n"
        f"<b>MODE: {mode}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"• <b>Max Daily Drawdown:</b> <code>{l.get('max_drawdown_pct', 10.0)}%</code>\n"
        f"• <b>Max Consecutive Losses (Per Bot):</b> <code>{l.get('max_consecutive_losses', 5)}</code>\n"
        f"• <b>Max Fleet Concurrent Positions:</b> <code>{l.get('max_concurrent_positions', 10)}</code>\n"
        f"• <b>Single Coin Asset Lock:</b> <code>ENABLED</code>\n"
        f"• <b>Statutory Friction Multiplier:</b> <code>1.01572x</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"<i>Risk formulas are immutable and enforced before every order dispatch.</i>"
    )


def format_telegram_alerts(alerts: list[dict[str, Any]], mode: str = "PAPER") -> str:
    """Format /alerts feed."""
    lines = [
        f"🚨 <b>ACTIVE ALERTS & SYSTEM WARNINGS ({len(alerts)})</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not alerts:
        lines.append("<i>No active alerts. All systems operational.</i>")
    else:
        for a in alerts[:8]:
            ts = a.get("logged_at", "")[:19]
            ev = a.get("event_type", "ALERT")
            src = a.get("source_service", "SYSTEM")
            lines.append(f"• [<code>{ts}</code>] <b>{ev}</b> ({src})")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_logs(logs: list[dict[str, Any]], mode: str = "PAPER") -> str:
    """Format /logs feed with strict secret masking."""
    lines = [
        f"📜 <b>OPERATIONAL EVENT LOGS (LAST {len(logs)})</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    if not logs:
        lines.append("<i>No operational logs recorded.</i>")
    else:
        for entry in logs[:10]:
            ts = str(entry.get("logged_at", ""))[:19]
            ev = entry.get("event_type", "EVENT")
            src = entry.get("source_service", "core")
            payload = entry.get("payload", {})
            snippet = str(payload)[:60]
            masked_snippet = mask_sensitive_data(snippet)
            lines.append(
                f"• <code>{ts}</code> <b>{ev}</b> ({src})\n   <i>{masked_snippet}</i>"
            )
    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_balance(b: dict[str, Any]) -> str:
    """Format dedicated /balance response with live exchange or paper pool funds."""
    mode = b.get("mode", "PAPER")
    is_live = mode in ("LIVE", "LIVE_MICROCASH")

    if is_live:
        inr_avail = float(b.get("inr_balance", 0.0))
        inr_locked = float(b.get("inr_locked", 0.0))
        total_inr = inr_avail + inr_locked
        assets = b.get("asset_balances", {})

        lines = [
            "💼 <b>COINDCX LIVE BROKER BALANCES</b>",
            "🔴 <b>MODE: LIVE PRODUCTION</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Available Cash (INR):</b> <code>₹{inr_avail:,.2f}</code>",
            f"• <b>Locked in Orders (INR):</b> <code>₹{inr_locked:,.2f}</code>",
            f"• <b>Total INR Cash:</b> <b>₹{total_inr:,.2f}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            "<b>Crypto Asset Holdings:</b>",
        ]
        if assets and any(v > 0.000001 for v in assets.values()):
            for coin, qty in sorted(assets.items()):
                if qty > 0.000001:
                    lines.append(f"  • <b>{coin}:</b> <code>{format_qty(qty)}</code>")
        else:
            lines.append("  • <i>No spot crypto assets currently held</i>")

        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"• <b>Source:</b> <code>COINDCX_EXCHANGE (Verified API)</code>")
        return "\n".join(lines)
    else:
        pool_limit = float(b.get("capital_limit", 10000.0) or 10000.0)
        deployed = float(b.get("deployed_capital", 0.0))
        avail = max(0.0, pool_limit - deployed)
        open_cnt = int(b.get("open_positions_count", 0))

        return (
            "📊 <b>PAPER SIMULATION WALLET</b>\n"
            "🟡 <b>MODE: 24/7 PAPER SIMULATION</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Virtual Capital Pool:</b> <code>₹{pool_limit:,.2f}</code>\n"
            f"• <b>Available Simulation Cash:</b> <code>₹{avail:,.2f}</code>\n"
            f"• <b>Deployed in Paper Trades:</b> <code>₹{deployed:,.2f}</code>\n"
            f"• <b>Active Virtual Positions:</b> <code>{open_cnt}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "• <b>Source:</b> <code>VIRTUAL_PAPER_POOL</code>"
        )


def format_telegram_qpb(metrics: dict[str, Any], view: str = "portfolio") -> str:
    """Format read-only QPB portfolio monitoring."""
    if view == "exposure":
        lines = ["<b>QPB PORTFOLIO EXPOSURE</b>", "<b>By Strategy:</b>"]
        for name, value in metrics.get("exposure_by_strategy", {}).items():
            lines.append(f"  - <b>{name}:</b> <code>INR {float(value):,.2f}</code>")
        lines.append("<b>By Coin:</b>")
        for name, value in metrics.get("exposure_by_coin", {}).items():
            lines.append(f"  - <b>{name}:</b> <code>INR {float(value):,.2f}</code>")
        if not metrics.get("exposure_by_coin"):
            lines.append("  - <i>No active coin exposure.</i>")
        return "\n".join(lines)
    if view == "allocation":
        lines = ["<b>QPB CAPITAL ALLOCATION</b>"]
        for name, value in metrics.get("allocation_by_strategy", {}).items():
            lines.append(f"  - <b>{name}:</b> <code>INR {float(value):,.2f}</code>")
        lines.append(f"<b>Available:</b> <code>INR {float(metrics.get('available_capital', 0.0)):,.2f}</code>")
        return "\n".join(lines)
    if view == "risk":
        limits = metrics.get("risk_limits", {})
        return (
            "<b>QPB PORTFOLIO RISK</b>\n"
            f"- Max positions: <code>{limits.get('max_total_positions', 0)}</code>\n"
            f"- Max utilization: <code>{float(limits.get('max_utilization_pct', 0.0)):.1f}%</code>\n"
            f"- Max position: <code>{float(limits.get('max_position_pct', 0.0)):.1f}%</code>\n"
            f"- Max strategy: <code>{float(limits.get('max_strategy_pct', 0.0)):.1f}%</code>\n"
            f"- Max coin: <code>{float(limits.get('max_coin_pct', 0.0)):.1f}%</code>\n"
            f"- Min order: <code>INR {float(limits.get('minimum_order_size', 0.0)):,.2f}</code>\n"
            f"- Min R:R: <code>{float(limits.get('minimum_risk_reward', 0.0)):.2f}</code>\n"
            f"- Coin lock: <code>{'ON' if limits.get('single_coin_lock') else 'OFF'}</code>"
        )
    return (
        "<b>QPB PAPER PORTFOLIO</b>\n"
        f"- Total capital: <code>INR {float(metrics.get('total_capital', 0.0)):,.2f}</code>\n"
        f"- Deployed: <code>INR {float(metrics.get('deployed_capital', 0.0)):,.2f}</code>\n"
        f"- Available: <code>INR {float(metrics.get('available_capital', 0.0)):,.2f}</code>\n"
        f"- Utilization: <code>{float(metrics.get('utilization_pct', 0.0)):.2f}%</code>\n"
        f"- Positions / closed: <code>{metrics.get('open_positions', 0)} / {metrics.get('closed_trades', 0)}</code>\n"
        f"- Realized P&L: <code>INR {float(metrics.get('realized_pnl', 0.0)):+,.2f}</code>\n"
        f"- Unrealized P&L: <code>INR {float(metrics.get('unrealized_pnl', 0.0)):+,.2f}</code>\n"
        f"- Total P&L: <code>INR {float(metrics.get('total_pnl', 0.0)):+,.2f}</code>\n"
        f"- Win rate: <code>{float(metrics.get('win_rate', 0.0)):.2f}%</code>\n"
        f"- Profit factor: <code>{float(metrics.get('profit_factor', 0.0)):.4f}</code>\n"
        f"- Rejected opportunities: <code>{len(metrics.get('rejected_opportunities', []))}</code>"
    )


def format_telegram_portfolio(p: dict[str, Any]) -> str:
    """Format dedicated /portfolio valuation and risk exposure."""
    mode = p.get("mode", "PAPER")
    is_live = mode in ("LIVE", "LIVE_MICROCASH")

    # Handle legacy portfolio snapshot dictionary if passed from unit tests
    if "total_aum" in p or "total_cash" in p:
        aum = float(p.get("total_aum", 0.0))
        dep = float(p.get("total_deployed", 0.0))
        cash = float(p.get("total_cash", 0.0))
        u_pnl = float(p.get("total_unrealised_pnl", 0.0))
        r_pnl = float(p.get("total_realised_pnl", 0.0))
        return (
            "📊 <b>PORTFOLIO & CAPITAL ALLOCATION</b>\n"
            f"<b>MODE: {mode}</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            f"• <b>Total Portfolio AUM:</b> <code>₹{aum:,.2f}</code>\n"
            f"• <b>Available Cash:</b> <code>₹{cash:,.2f}</code>\n"
            f"• <b>Deployed Capital:</b> <code>₹{dep:,.2f}</code>\n"
            f"• <b>Realized P&L:</b> <code>₹{r_pnl:,.2f}</code>\n"
            f"• <b>Unrealized P&L:</b> <code>₹{u_pnl:,.2f}</code>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    open_pos = p.get("open_positions", [])
    deployed = float(p.get("deployed_capital", 0.0))
    realized_pnl = float(p.get("realized_pnl", 0.0))
    unrealized_pnl = float(p.get("unrealized_pnl", 0.0))
    total_pnl = realized_pnl + unrealized_pnl

    real_icon = "🟢" if realized_pnl >= 0 else "🔴"
    unreal_icon = "🟢" if unrealized_pnl >= 0 else "🔴"
    tot_icon = "🟢" if total_pnl >= 0 else "🔴"

    if is_live:
        cash = float(p.get("available_cash", 0.0))
        nav = cash + deployed + unrealized_pnl
        exposure_pct = (deployed / nav * 100.0) if nav > 0 else 0.0

        lines = [
            "📊 <b>PORTFOLIO & CAPITAL ALLOCATION</b>",
            "🔴 <b>MODE: LIVE PRODUCTION</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Net Portfolio NAV:</b> <b>₹{nav:,.2f}</b>",
            f"• <b>Free Cash Balance:</b> <code>₹{cash:,.2f}</code>",
            f"• <b>Active Capital Deployed:</b> <code>₹{deployed:,.2f}</code> ({exposure_pct:.1f}% Exposure)",
            f"• <b>Open Market Positions:</b> <code>{len(open_pos)}</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>24h Realized P&L:</b> {real_icon} ₹{realized_pnl:,.2f}",
            f"• <b>Live Unrealized P&L:</b> {unreal_icon} ₹{unrealized_pnl:,.2f}",
            f"• <b>Net Combined P&L:</b> {tot_icon} <b>₹{total_pnl:,.2f}</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
        ]
        if open_pos:
            lines.append("<b>Active Live Holdings:</b>")
            for pos in open_pos[:5]:
                sym = pos.get("coin", "COIN")
                bot = pos.get("bot", "BOT")
                u_pnl = float(pos.get("unrealized_pnl", 0.0))
                u_pct = float(pos.get("unrealized_pnl_pct", 0.0))
                p_icon = "🟢" if u_pnl >= 0 else "🔴"
                lines.append(f"  • <b>{sym}</b> ({bot}): {p_icon} ₹{u_pnl:+,.2f} ({u_pct:+.2f}%)")
        else:
            lines.append("<i>Zero active market positions — 100% dry powder reserve.</i>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)
    else:
        starting = float(p.get("starting_capital", 10000.0) or 10000.0)
        nav = starting + total_pnl
        trades_cnt = int(p.get("trades_count", 0))
        win_rate = float(p.get("win_rate_pct", 0.0))

        lines = [
            "📊 <b>PORTFOLIO & CAPITAL ALLOCATION</b>",
            "🟡 <b>MODE: 24/7 SIMULATION</b>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Virtual Starting Capital:</b> <code>₹{starting:,.2f}</code>",
            f"• <b>Current Simulation NAV:</b> <b>₹{nav:,.2f}</b>",
            f"• <b>Simulated Net P&L:</b> {tot_icon} <b>₹{total_pnl:+,.2f}</b>",
            f"• <b>Total Closed Trades:</b> <code>{trades_cnt}</code>",
            f"• <b>Rolling Win Rate:</b> <code>{win_rate:.1f}%</code>",
            "━━━━━━━━━━━━━━━━━━━━━━━━━",
            f"• <b>Active Virtual Positions:</b> <code>{len(open_pos)}</code>",
        ]
        if open_pos:
            for pos in open_pos[:5]:
                sym = pos.get("coin", "COIN")
                bot = pos.get("bot", "BOT")
                u_pnl = float(pos.get("unrealized_pnl", 0.0))
                u_pct = float(pos.get("unrealized_pnl_pct", 0.0))
                p_icon = "🟢" if u_pnl >= 0 else "🔴"
                lines.append(f"  • <b>{sym}</b> ({bot}): {p_icon} ₹{u_pnl:+,.2f} ({u_pct:+.2f}%)")
        else:
            lines.append("<i>No active virtual positions currently open.</i>")
        lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
        return "\n".join(lines)


def format_telegram_winrate(w: dict[str, Any]) -> str:
    """Format dedicated /winrate and /stats strategy performance report."""
    mode = w.get("mode", "PAPER")
    total_trades = int(w.get("trades_count", 0))
    wins = int(w.get("wins_count", 0))
    losses = total_trades - wins
    win_rate = float(w.get("win_rate_pct", 0.0))
    realized_pnl = float(w.get("realized_pnl", 0.0))
    avg_win = float(w.get("avg_win_inr", 0.0))
    avg_loss = float(w.get("avg_loss_inr", 0.0))
    profit_factor = float(w.get("profit_factor", 0.0))
    signals_evaluated = int(w.get("signals_evaluated", 0))

    wr_icon = "🟢" if win_rate >= 60.0 else "🟡" if win_rate >= 45.0 else "🔴"
    pnl_icon = "🟢" if realized_pnl >= 0 else "🔴"

    lines = [
        "🏆 <b>24/7 STRATEGY WIN RATE & PERFORMANCE</b>",
        f"<b>MODE: {mode}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"• <b>Rolling Win Rate:</b> {wr_icon} <b>{win_rate:.1f}%</b>",
        f"• <b>Total Closed Trades:</b> <code>{total_trades}</code> (Wins: <code>{wins}</code> | Losses: <code>{losses}</code>)",
        f"• <b>Total Realized P&L:</b> {pnl_icon} <b>₹{realized_pnl:+,.2f}</b> (post-statutory friction)",
        f"• <b>Average Win:</b> <code>+₹{avg_win:,.2f}</code>",
        f"• <b>Average Loss:</b> <code>-₹{avg_loss:,.2f}</code>",
        f"• <b>Profit Factor:</b> <code>{profit_factor:.2f}</code>",
        f"• <b>Total Signals Evaluated:</b> <code>{signals_evaluated}</code>",
        "━━━━━━━━━━━━━━━━━━━━━━━━━",
        "<b>Strategy Breakdown:</b>",
    ]
    per_strategy = w.get("strategy_stats", {})
    if per_strategy:
        for strat, stats in per_strategy.items():
            s_trades = stats.get("trades", 0)
            s_wr = stats.get("win_rate_pct", 0.0)
            s_pnl = stats.get("pnl", 0.0)
            s_icon = "🟢" if s_wr >= 60.0 else "🟡" if s_wr >= 45.0 else "⚪"
            lines.append(f"  • <b>{strat}:</b> {s_icon} <code>{s_wr:.1f}%</code> ({s_trades} trades | ₹{s_pnl:+,.2f})")
    else:
        lines.append("  • <i>STE: SuperTrend ATR Breakout</i>")
        lines.append("  • <i>HDA: High Delivery Absorption</i>")
        lines.append("  • <i>VCP: Volatility Contraction</i>")
        lines.append("  • <i>BBS: Bollinger Band Squeeze</i>")

    lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def format_telegram_help(mode: str = "PAPER") -> str:
    """Format comprehensive help manual tailored to LIVE or PAPER bot."""
    if mode == "SCANNER":
        return (
            "📖 <b>PROJECT-ALPHA SCANNER BOT MANUAL</b>\n"
            "📡 <b>MODE: MARKET INTELLIGENCE & CONFLUENCE SCANNER</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>1. Scanner & Signal Commands:</b>\n"
            "  • /scan — Latest market scan & candidate rankings\n"
            "  • /signals — Active high-conviction MTF signals\n"
            "  • /signal &lt;coin&gt; — In-depth technical breakdown (e.g. /signal DOT)\n"
            "  • /funnel — 5-Layer filter funnel conversion stats\n"
            "  • /watchlist — Active filtered coin watchlist\n\n"
            "<b>2. Quality & Win Rate Commands:</b>\n"
            "  • /winrate — Forward signal performance & trajectory stats\n"
            "  • /stats — Confluence quality & cycle metrics\n\n"
            "<b>3. System & Health Commands:</b>\n"
            "  • /start /menu — Interactive button control menu\n"
            "  • /status — Scanner engine status & cycle count\n"
            "  • /health — Market feed & database connectivity probe\n"
            "  • /uptime — Scanner process uptime\n"
            "  • /help — Show this scanner manual\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Note: Trade execution is handled separately by the Paper Bot (@AlphaMBTBot).</i>"
        )

    is_live = mode in ("LIVE", "LIVE_MICROCASH")

    if is_live:
        return (
            "📖 <b>PROJECT-ALPHA OPERATOR COMMANDS</b>\n"
            "🔴 <b>MODE: LIVE PRODUCTION (COINDCX)</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>1. Live Wallet & Balance Commands:</b>\n"
            "  • /balance — Real-Time CoinDCX INR & Crypto Balances\n"
            "  • /portfolio — Live Fleet Valuation & Asset Exposure\n"
            "  • /capital — Dynamic Broker Capital & Free Margin\n"
            "  • /pnl — Realized & Unrealized Live P&L\n\n"
            "<b>2. Live Execution & Position Commands:</b>\n"
            "  • /positions — Active Market Positions with Trailing Stops\n"
            "  • /orders — Live Exchange Orders & Execution Fills\n"
            "  • /trades — Realized Trade Logs & Fill Prices\n\n"
            "<b>3. Live Safety & Emergency Commands:</b>\n"
            "  • /status — Production Fleet Health & Telemetry\n"
            "  • /bots — Active bot fleet status\n"
            "  • /risk — Circuit Breaker & Safety Ceilings\n"
            "  • /limits — Micro-Order Caps (₹200.00 Limit)\n"
            "  • /pause — Pause New Live Entries\n"
            "  • /resume — Resume Live Automated Execution\n"
            "  • /emergency_stop — Trip circuit breaker & freeze trading\n"
            "  • /kill — Emergency Freeze & Trip Kill Switch\n"
            "  • /reconcile — Audit Local Ledger vs Exchange State\n\n"
            "<b>4. Monitoring & System Commands:</b>\n"
            "  • /start — Welcome & Operator Overview\n"
            "  • /help — Show this complete manual\n"
            "  • /health — Subsystem Diagnostic Probes\n"
            "  • /alerts — Active Live Alerts & Warnings\n"
            "  • /logs — Scrubbed Audit Trail\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
    else:
        return (
            "📖 <b>PROJECT-ALPHA OPERATOR COMMANDS</b>\n"
            "🟡 <b>MODE: 24/7 SIMULATION & WIN RATE ENGINE</b>\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "<b>1. Strategy Success & Win Rate Commands:</b>\n"
            "  • /winrate — 24/7 Rolling Win Rate % & Net R:R\n"
            "  • /stats — Detailed Performance & Trade Expectancy\n"
            "  • /portfolio — ₹10,000 Paper Capital & Simulated NAV\n"
            "  • /balance — Paper Simulation Capital Pool\n"
            "  • /pnl — Simulated Realized & Unrealized P&L\n\n"
            "<b>2. Multi-Timeframe Scanner Commands:</b>\n"
            "  • /signals — Active High-Conviction MTF Signals (80-89 & 90+ Elite)\n"
            "  • /scan — Latest Scanner Cycle & Candidate Rankings\n"
            "  • /signal &lt;coin&gt; — In-Depth Coin Technical Breakdown\n"
            "  • /funnel — 5-Layer Filter Conversion Statistics\n"
            "  • /watchlist — Active Filtered Coin Watchlist\n\n"
            "<b>3. Virtual Simulation Execution Commands:</b>\n"
            "  • /positions — Active Virtual Paper Trades & Live Trailing Stops\n"
            "  • /trades — Simulated Trade History Ledger\n"
            "  • /orders — Simulated Order Queue & Fills\n\n"
            "<b>4. Engine & System Commands:</b>\n"
            "  • /start — Welcome & Operator Overview\n"
            "  • /help — Show this complete manual\n"
            "  • /status — Paper Simulation Health & Cycle Stats\n"
            "  • /bots — Active bot fleet status\n"
            "  • /emergency_stop — Trip circuit breaker & freeze trading\n"
            "  • /health — Subsystem Integrity Check\n"
            "  • /config — Active Scanner & Simulation Settings\n"
            "  • /db — Export Simulation Database for Analysis\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
