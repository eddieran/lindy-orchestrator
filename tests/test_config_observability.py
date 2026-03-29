from lindy_orchestrator.config import ObservabilityConfig, OrchestratorConfig


class TestObservabilityConfig:
    def test_defaults(self) -> None:
        cfg = ObservabilityConfig()

        assert cfg.level == 1
        assert cfg.retention_days == 30

    def test_orchestrator_config_includes_observability_defaults(self) -> None:
        cfg = OrchestratorConfig()

        assert cfg.observability == ObservabilityConfig()

    def test_orchestrator_config_accepts_observability_overrides(self) -> None:
        cfg = OrchestratorConfig.model_validate(
            {"observability": {"level": 3, "retention_days": 14}}
        )

        assert cfg.observability.level == 3
        assert cfg.observability.retention_days == 14
