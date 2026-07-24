import os

import pytest
from fastapi.testclient import TestClient

from knowledge.api.app import create_app


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.getenv("RUN_AGENT_LIVE_SMOKE") != "1",
        reason="set RUN_AGENT_LIVE_SMOKE=1 with explicit test credentials",
    ),
]


def test_live_agent_greeting_smoke():
    with TestClient(create_app()) as client:
        response = client.post("/api/v1/agent/chat", json={"message": "你好"})

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["answer"]
