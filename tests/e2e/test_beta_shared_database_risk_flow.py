from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from core.database import open_beta_database
from core.repository.beta_store import BetaStore
from core.repository.paper_execution_repo import PaperExecutionRepository
from services.paper_bot.risk import DailyRiskState, PaperRiskPolicy


@pytest.mark.asyncio
async def test_three_connections_share_persisted_emergency_stop(tmp_path):
    path = tmp_path / "project_beta.db"
    scanner = await open_beta_database(str(path))
    await BetaStore(scanner).initialize()
    paper = await open_beta_database(str(path))
    learner = await open_beta_database(str(path))
    repo = PaperExecutionRepository(paper)
    await repo.initialize_account(Decimal("15000"))
    now = datetime(2026, 10, 16, 5, tzinfo=timezone.utc)
    stopped = DailyRiskState(
        date(2026, 10, 16), Decimal("-1000"), Decimal("0"), Decimal("0"), 3,
        "EMERGENCY_STOP",
    )
    await repo.save_daily_risk_state(stopped, now)

    scanner_view = await PaperExecutionRepository(scanner).get_daily_risk_state(stopped.trading_date, {})
    learner_view = await PaperExecutionRepository(learner).get_daily_risk_state(stopped.trading_date, {})

    assert PaperRiskPolicy().evaluate_daily_state(scanner_view).code == "DAILY_LOSS_LIMIT_REACHED"
    assert learner_view.state == "EMERGENCY_STOP"
    assert scanner_view.loss == Decimal("1000")
    await scanner.close()
    await paper.close()
    await learner.close()
