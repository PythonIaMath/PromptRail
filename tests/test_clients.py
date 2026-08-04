from __future__ import annotations

import json

from promptrail import clients
from promptrail.clients import LeRouterPolicyGenerator
from promptrail.models import OperatingPolicy


def test_lerouter_policy_generator_sends_strict_compatible_schema(monkeypatch):
    captured = {}

    def fake_post_json(**kwargs):
        captured.update(kwargs["payload"])
        return {
            "response": {
                "content": json.dumps(
                    {
                        "instruction": "Stay inside the supplied budgets.",
                        "workflow_cost_budget_usd": 1,
                        "workflow_latency_budget_ms": 5000,
                        "expected_llm_calls": 2,
                    }
                )
            }
        }

    monkeypatch.setattr(clients, "_post_json", fake_post_json)
    generator = LeRouterPolicyGenerator(
        api_url="https://lerouter.example",
        agent_token="test-token",
        user_id="test-user",
    )

    generated = generator.generate_policy(
        system_instruction="Generate policy.",
        enterprise_data={"enterprise.json": {"budget": 1}},
        output_schema=OperatingPolicy.model_json_schema(),
    )

    assert generated["expected_llm_calls"] == 2
    schema = captured["response_format"]["json_schema"]["schema"]
    assert schema["required"] == list(schema["properties"])
    task_rule = schema["$defs"]["TaskRule"]
    assert task_rule["required"] == list(task_rule["properties"])
    assert task_rule["additionalProperties"] is False
    assert "default" not in schema["properties"]["source_digest"]
