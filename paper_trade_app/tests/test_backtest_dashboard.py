from fastapi.testclient import TestClient

from paper_trade_app.backtest_dashboard import app


client = TestClient(app)


def test_overview_matches_packaged_workbook():
    response = client.get("/api/backtest/overview")
    assert response.status_code == 200
    data = response.json()
    assert data["scenario_count"] == 219
    assert data["active_scenario_count"] == 11
    assert data["trade_count"] == 54325
    assert data["active_scenario_ids"] == [5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111]


def test_active_scenario_filter():
    response = client.get("/api/backtest/scenarios", params={"active_only": "true"})
    assert response.status_code == 200
    data = response.json()
    assert data["count"] == 11
    assert {row["scenario_id"] for row in data["items"]} == {
        5, 9, 10, 15, 19, 49, 56, 64, 79, 108, 111
    }

