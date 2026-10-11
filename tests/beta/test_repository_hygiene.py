from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_no_live_order_or_vps_deployment_surface():
    assert not (ROOT / "services" / "live_bot").exists()
    assert not (ROOT / "services" / "live").exists()
    assert not (ROOT / "shared" / "execution" / "brokers").exists()
    deploy_files = [path for path in (ROOT / "deploy").rglob("*") if path.is_file()]
    assert not any("vps" in path.name.lower() for path in deploy_files)


def test_active_configuration_contains_no_order_credentials():
    text = "\n".join(
        (ROOT / path).read_text(encoding="utf-8")
        for path in ["shared/core/config.py", "config/beta.env.example", "README.md"]
    ).lower()
    for forbidden in ("zerodha_api_key", "coindcx_api_key", "place_order", "live_bot"):
        assert forbidden not in text


def test_local_compose_has_exact_three_service_order_and_read_only_mounts():
    compose = (ROOT / "deploy/docker/docker-compose.yml").read_text(encoding="utf-8")
    assert compose.index("beta-scanner:") < compose.index("beta-paper:") < compose.index("beta-learner:")
    assert compose.count(":ro") == 3
    assert "alpha-" not in compose.lower()
    assert "live" not in compose.lower()
