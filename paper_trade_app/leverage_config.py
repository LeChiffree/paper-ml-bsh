from __future__ import annotations


LEVERAGE_BY_SCENARIO: dict[int, int] = {
    **{scenario_id: 10 for scenario_id in (38, 53, 59, 91, 115, 125, 126, 129, 134, 143, 165)},
    **{scenario_id: 5 for scenario_id in (32, 61, 117, 147, 149, 155, 158, 160)},
    **{scenario_id: 2 for scenario_id in (20, 43, 114)},
}

