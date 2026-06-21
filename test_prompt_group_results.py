"""Test that _build_prompt embeds the finished group results.

Reuses the mock-API approach: patches urllib so _get_group_matches (called from
inside _build_prompt) receives a fake /api/matches payload with two finished
intra-group results, and asserts those scorelines appear in the prompt while the
unrelated group's results do not.

Run:  python test_prompt_group_results.py
"""
import io
import json
import sys
from contextlib import contextmanager
from unittest import mock

import predict


FAKE_MATCHES = [
    # --- Tunisia/Japan group: two FINISHED, rest scheduled ---
    {"match_id": 1, "home_team": "Spain", "away_team": "Mexico",
     "status": "finished", "result_home": 1, "result_away": 0},
    {"match_id": 2, "home_team": "Tunisia", "away_team": "Spain",
     "status": "finished", "result_home": 0, "result_away": 2},
    {"match_id": 3, "home_team": "Tunisia", "away_team": "Japan",
     "status": "scheduled", "result_home": None, "result_away": None},
    {"match_id": 4, "home_team": "Japan", "away_team": "Mexico",
     "status": "scheduled", "result_home": None, "result_away": None},
    # --- Unrelated group: finished, must NOT appear ---
    {"match_id": 9, "home_team": "Brazil", "away_team": "France",
     "status": "finished", "result_home": 2, "result_away": 2},
]


@contextmanager
def mock_matches_api(payload):
    def fake_urlopen(url, timeout=10):
        body = json.dumps(payload).encode()

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *exc):
                return False

        return _Resp(body)

    with mock.patch.object(predict.urllib.request, "urlopen", side_effect=fake_urlopen):
        yield


def run():
    match = {
        "home_team": "Tunisia",
        "away_team": "Japan",
        "stage": "group",
        "api_url": "http://localhost:8000",
    }

    with mock_matches_api(FAKE_MATCHES):
        prompt = predict._build_prompt(match)

    print("----- group-results section of prompt -----")
    for line in prompt.splitlines():
        if "-" in line and any(t in line for t in ("Spain", "Mexico", "Tunisia", "Japan", "Brazil")):
            print(line)
    print("-------------------------------------------")

    ok = True

    # Finished intra-group results must be present.
    for expected in ("Spain 1-0 Mexico", "Tunisia 0-2 Spain"):
        if expected in prompt:
            print(f"PASS: contains '{expected}'")
        else:
            ok = False
            print(f"FAIL: missing '{expected}'")

    # The header line should be present.
    if "results already played in this group" in prompt:
        print("PASS: group-results header present")
    else:
        ok = False
        print("FAIL: group-results header missing")

    # Scheduled (result-less) fixtures must NOT be rendered as scorelines.
    if "Tunisia None" not in prompt and "None-None" not in prompt:
        print("PASS: scheduled fixtures not rendered")
    else:
        ok = False
        print("FAIL: scheduled fixtures leaked into prompt")

    # Unrelated group's finished result must NOT appear.
    if "Brazil 2-2 France" not in prompt:
        print("PASS: unrelated group result excluded")
    else:
        ok = False
        print("FAIL: unrelated group result leaked into prompt")

    print("\nRESULT:", "PASS \u2705" if ok else "FAIL \u274c")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())

