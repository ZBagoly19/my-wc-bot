"""Integration test for predict.predict_score: LLM-first with heuristic fallback.

Exercises both paths without hitting the real LLM proxy or league API:
  1. LLM returns a valid score   -> predict_score uses it.
  2. LLM unavailable (no proxy)  -> predict_score falls back to the heuristic,
                                    which (with no finished matches) returns the
                                    safe default.

Run:  python test_predict_score.py
"""
import sys
from unittest import mock

import predict


def test_llm_path_used_when_available():
    match = {
        "home_team": "Tunisia",
        "away_team": "Japan",
        "stage": "group",
        "llm_proxy_url": "https://llm-proxy.corp.local/v1",
        "llm_api_key": "test-token",
        "api_url": "http://localhost:8000",
    }
    # Patch _ask_llm so no real network call happens.
    with mock.patch.object(predict, "_ask_llm", return_value={"home_goals": 0, "away_goals": 2}):
        result = predict.predict_score(match)
    assert result == (0, 2), f"expected (0, 2) from LLM, got {result}"
    print("PASS: LLM prediction is used when available ->", result)


def test_falls_back_to_heuristic_when_llm_unavailable():
    # No llm_proxy_url -> _ask_llm returns None internally; no api_url -> heuristic
    # has no finished matches -> safe default (DEFAULT_GOALS_WINNER, DEFAULT_GOALS_LOSER).
    match = {"home_team": "Tunisia", "away_team": "Japan", "stage": "group"}
    result = predict.predict_score(match)
    expected = (predict.DEFAULT_GOALS_WINNER, predict.DEFAULT_GOALS_LOSER)
    assert result == expected, f"expected fallback {expected}, got {result}"
    print("PASS: falls back to heuristic default when LLM unavailable ->", result)


def test_falls_back_to_heuristic_when_llm_errors():
    # _ask_llm returns None (simulating a failed/unparseable LLM call); still no
    # api_url, so heuristic returns the safe default.
    match = {
        "home_team": "Tunisia",
        "away_team": "Japan",
        "stage": "group",
        "llm_proxy_url": "https://llm-proxy.corp.local/v1",
    }
    with mock.patch.object(predict, "_ask_llm", return_value=None):
        result = predict.predict_score(match)
    expected = (predict.DEFAULT_GOALS_WINNER, predict.DEFAULT_GOALS_LOSER)
    assert result == expected, f"expected fallback {expected}, got {result}"
    print("PASS: falls back to heuristic when LLM returns None ->", result)


def run():
    tests = [
        test_llm_path_used_when_available,
        test_falls_back_to_heuristic_when_llm_unavailable,
        test_falls_back_to_heuristic_when_llm_errors,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            failed += 1
            print("FAIL:", t.__name__, "-", e)
    print("\nRESULT:", "PASS \u2705" if failed == 0 else f"FAIL \u274c ({failed} failed)")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run())

