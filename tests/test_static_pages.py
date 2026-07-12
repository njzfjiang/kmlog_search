import sys
from pathlib import Path

from fastapi.testclient import TestClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from servers.app import app  # noqa: E402


def test_reviewed_memory_page_is_served():
    client = TestClient(app)

    response = client.get("/reviewed_memory")

    assert response.status_code == 200
    assert "Reviewed Memory" in response.text
    assert "static/reviewed_memory.js" in response.text
