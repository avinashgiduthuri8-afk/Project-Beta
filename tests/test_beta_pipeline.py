"""Unit tests for BETA 13-Stage Automated Execution & Intelligence Pipeline."""

import pytest
from pipeline.beta_pipeline import BetaPipelineOrchestrator, get_beta_pipeline
from brokers.paper_broker import PaperBroker
from core.enums import OrderStatus


def test_beta_pipeline_initialization():
    pipeline = BetaPipelineOrchestrator(auto_execution_enabled=True)
    status = pipeline.get_pipeline_status()
    
    assert status["system_name"] == "BETA 13-Stage Algorithmic Execution System"
    assert status["auto_execution_enabled"] is True
    assert "stages" in status
    assert len(status["stages"]) == 13
    assert "1_market_data" in status["stages"]
    assert "13_improved_strategy" in status["stages"]


def test_beta_pipeline_execution_cycle():
    broker = PaperBroker(initial_capital=200000.0, slippage_pct=0.0)
    pipeline = BetaPipelineOrchestrator(broker=broker, auto_execution_enabled=True)

    result = pipeline.run_pipeline_cycle()
    assert "cycle_id" in result
    assert "duration_ms" in result
    assert "stages" in result
    assert result["candidates_scanned"] >= 0

    # Verify stage telemetry states
    stages = result["stages"]
    assert stages["1_market_data"]["status"] == "ACTIVE"
    assert stages["2_scanner"]["status"] == "COMPLETED"
    assert stages["3_signal_engine"]["status"] == "COMPLETED"
    assert stages["4_ai_intelligence"]["status"] == "COMPLETED"
    assert stages["5_trade_constructor"]["status"] == "COMPLETED"
    assert stages["6_risk_engine"]["status"] == "COMPLETED"
    assert stages["7_execution_engine"]["status"] == "COMPLETED"
    assert stages["8_position_manager"]["status"] == "MONITORING"
    assert stages["9_trade_journal"]["status"] == "RECORDING"
    assert stages["10_analytics"]["status"] == "UPDATED"
    assert stages["13_improved_strategy"]["status"] == "CALIBRATED"


def test_beta_pipeline_toggle_auto():
    pipeline = BetaPipelineOrchestrator(auto_execution_enabled=True)
    assert pipeline.toggle_auto_execution() is False
    assert pipeline.toggle_auto_execution() is True
    assert pipeline.toggle_auto_execution(False) is False
    assert pipeline.toggle_auto_execution(True) is True
