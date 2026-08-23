"""Unit tests for LLM provider registry, circuit breaker, and fallback logic."""

import os
from unittest.mock import MagicMock, patch
import pytest
from src.agent.llm_providers import (
    CircuitBreaker,
    LLMProviderConfig,
    circuit_breaker,
    get_active_providers,
)
from src.models import UserContext
from src.agent.loop import AgentOrchestrator


def test_circuit_breaker_tripping():
    cb = CircuitBreaker(failure_threshold=3, cooldown_minutes=5)
    p_name = "test_provider"

    assert cb.is_available(p_name) is True

    cb.record_failure(p_name)
    assert cb.is_available(p_name) is True

    cb.record_failure(p_name)
    assert cb.is_available(p_name) is True

    # 3rd failure trips the circuit breaker
    cb.record_failure(p_name)
    assert cb.is_available(p_name) is False


def test_circuit_breaker_recovery():
    cb = CircuitBreaker(failure_threshold=3, cooldown_minutes=5)
    p_name = "test_provider"

    cb.record_failure(p_name)
    cb.record_failure(p_name)
    cb.record_success(p_name)
    assert cb.is_available(p_name) is True

    # Reset circuit breaker
    cb.reset()
    assert cb.is_available(p_name) is True


def test_get_active_providers_skips_missing_keys(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key-12345")

    circuit_breaker.reset()
    active = get_active_providers()

    names = [cfg.name for cfg, key, model in active]
    assert "groq" not in names
    assert "nvidia_nim" in names


def test_fallback_to_deterministic_when_all_fail(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "")

    circuit_breaker.reset()
    orchestrator = AgentOrchestrator()
    ctx = UserContext(role="customer", account_id="ACCT-001")
    res = orchestrator.run(ctx=ctx, query="What is the status of ticket TKT-501?")

    assert res.handled_by == "deterministic"
    assert "Support Ticket `TKT-501`" in res.answer


