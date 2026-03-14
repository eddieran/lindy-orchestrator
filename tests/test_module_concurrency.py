"""Tests for per-module concurrency semaphores."""

from __future__ import annotations

import threading

from lindy_orchestrator.config import OrchestratorConfig, SafetyConfig


class TestModuleConcurrency:
    def test_semaphore_created_per_module(self) -> None:
        cfg = OrchestratorConfig()
        cfg.safety.module_concurrency = {"backend": 1, "frontend": 2}
        sems = {
            mod: threading.Semaphore(limit) for mod, limit in cfg.safety.module_concurrency.items()
        }
        assert "backend" in sems
        assert "frontend" in sems
        # backend semaphore has limit 1: acquire succeeds, second would block
        assert sems["backend"].acquire(blocking=False) is True
        assert sems["backend"].acquire(blocking=False) is False
        sems["backend"].release()
        # frontend semaphore has limit 2
        assert sems["frontend"].acquire(blocking=False) is True
        assert sems["frontend"].acquire(blocking=False) is True
        assert sems["frontend"].acquire(blocking=False) is False
        sems["frontend"].release()
        sems["frontend"].release()

    def test_no_semaphore_when_unconfigured(self) -> None:
        cfg = OrchestratorConfig()
        sems = {
            mod: threading.Semaphore(limit) for mod, limit in cfg.safety.module_concurrency.items()
        }
        assert sems == {}

    def test_config_roundtrip(self) -> None:
        cfg = SafetyConfig(module_concurrency={"api": 3})
        assert cfg.module_concurrency == {"api": 3}
        dumped = cfg.model_dump()
        restored = SafetyConfig.model_validate(dumped)
        assert restored.module_concurrency == {"api": 3}
