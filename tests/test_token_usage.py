"""Unit tests for dynamic TokenUsage models, parsing, and collection across providers."""

import pytest
from src.agent.loop import AgentOrchestrator
from src.models import AgentResponse, TokenUsage, UserContext


def test_token_usage_model_creation():
    tu = TokenUsage(
        provider="Groq",
        model="openai/gpt-oss-120b",
        prompt_tokens=1090,
        completion_tokens=398,
        total_tokens=1488,
        status="success",
    )
    assert tu.provider == "Groq"
    assert tu.model == "openai/gpt-oss-120b"
    assert tu.prompt_tokens == 1090
    assert tu.completion_tokens == 398
    assert tu.total_tokens == 1488
    assert tu.status == "success"


def test_agent_response_with_token_usages():
    resp = AgentResponse(
        answer="Test response",
        token_usages=[
            TokenUsage(
                provider="Groq",
                model="openai/gpt-oss-120b",
                prompt_tokens=1500,
                status="rate_limited",
                token_limit=200000,
            ),
            TokenUsage(
                provider="NVIDIA NIM",
                model="nvidia/nemotron-3-nano-30b-a3b",
                prompt_tokens=1090,
                completion_tokens=398,
                total_tokens=1488,
                status="success",
            ),
        ],
        handled_by="nvidia_nim",
    )
    assert len(resp.token_usages) == 2
    assert resp.token_usages[0].status == "rate_limited"
    assert resp.token_usages[1].status == "success"
    assert resp.token_usages[1].total_tokens == 1488


def test_orchestrator_deterministic_token_usage(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "")
    monkeypatch.setenv("NVIDIA_API_KEY", "")

    orchestrator = AgentOrchestrator()
    ctx = UserContext(role="customer", account_id="ACCT-001")
    resp = orchestrator.run(ctx=ctx, query="What is the status of ticket TKT-501?")

    assert resp.handled_by == "deterministic"
    # Even in deterministic fallback mode, token_usages list is present
    assert isinstance(resp.token_usages, list)
