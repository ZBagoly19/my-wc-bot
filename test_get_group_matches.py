"""Standalone test for predict._get_group_matches (Tunisia vs Japan).

The real league API is only reachable from inside the framework's network, so
this test mocks urllib's HTTP response with a realistic /api/matches payload:
a 4-team group (Tunisia, Japan, Spain, Mexico) plus an unrelated group
(Brazil, France, Croatia, Morocco) to prove the function filters by group.

Run:  python test_get_group_matches.py
"""
import io
import json
import sys
from contextlib import contextmanager
from unittest import mock

import predict


# A realistic World Cup-style fixture list. Two groups of four, each playing a
# full round-robin (6 matches per group). Tunisia + Japan share a group with
# Spain and Mexico. Some matches are finished, some scheduled.
GROUP_TJ = ["Tunisia", "Japan", "Spain", "Mexico"]
GROUP_OTHER = ["Brazil", "France", "Croatia", "Morocco"]

FAKE_MATCHES = [
    # --- Tunisia/Japan group ---
    {"match_id": 1, "home_team": "Spain", "away_team": "Mexico",
     "status": "finished", "result_home": 1, "result_away": 0},
    {"match_id": 2, "home_team": "Tunisia", "away_team": "Japan",
     "status": "scheduled", "result_home": None, "result_away": None},
    {"match_id": 3, "home_team": "Spain", "away_team": "Tunisia",
     "status": "scheduled", "result_home": None, "result_away": None},
    {"match_id": 4, "home_team": "Japan", "away_team": "Mexico",
     "status": "scheduled", "result_home": None, "result_away": None},
    {"match_id": 5, "home_team": "Japan", "away_team": "Spain",
     "status": "scheduled", "result_home": None, "result_away": None},
    {"match_id": 6, "home_team": "Mexico", "away_team": "Tunisia",
     "status": "scheduled", "result_home": None, "result_away": None},
    # --- Unrelated group (must be excluded) ---
    {"match_id": 7, "home_team": "Brazil", "away_team": "France",
     "status": "finished", "result_home": 2, "result_away": 2},
    {"match_id": 8, "home_team": "Croatia", "away_team": "Morocco",
     "status": "finished", "result_home": 0, "result_away": 1},
    {"match_id": 9, "home_team": "Brazil", "away_team": "Croatia",
     "status": "scheduled", "result_home": None, "result_away": None},
]


@contextmanager
def mock_matches_api(payload):
    """Patch urllib.request.urlopen so the function gets our fake JSON."""
    def fake_urlopen(url, timeout=10):
        body = json.dumps(payload).encode()
        resp = io.BytesIO(body)
        # urlopen is used as a context manager; BytesIO supports the protocol
        # but its __exit__ doesn't accept the (exc_type, exc, tb) it receives,
        # so wrap it minimally.
        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner
            def __exit__(self_inner, *exc):
                return False
        return _Resp(body)

    with mock.patch.object(predict.urllib.request, "urlopen", side_effect=fake_urlopen):
        yield


def run():
    api_url = "http://localhost:8000"
    home, away = "Tunisia", "Japan"

    with mock_matches_api(FAKE_MATCHES):
        result = predict._get_group_matches(api_url, home, away)

    returned_ids = sorted(m["match_id"] for m in result)
    returned_teams = sorted({m["home_team"] for m in result} |
                            {m["away_team"] for m in result})

    print(f"Returned {len(result)} matches; ids={returned_ids}")
    print(f"Teams appearing in returned matches: {returned_teams}")

    # What a CORRECT group-aware function should return: the 6 matches whose
    # match_id is 1..6 (all four group teams), and nothing from the other group.
    expected_ids = [1, 2, 3, 4, 5, 6]

    ok = True

    if returned_ids == expected_ids:
        print("PASS: returned exactly the 6 Tunisia/Japan group matches.")
    else:
        ok = False
        print(f"FAIL: expected ids {expected_ids}, got {returned_ids}.")

    leaked = [t for t in returned_teams if t in GROUP_OTHER]
    if leaked:
        ok = False
        print(f"FAIL: matches from the unrelated group leaked in (teams: {leaked}).")

    missing_group_teams = [t for t in GROUP_TJ if t not in returned_teams]
    if missing_group_teams:
        ok = False
        print(f"FAIL: group teams missing from results: {missing_group_teams}.")

    print("\nRESULT:", "PASS ✅" if ok else "FAIL ❌")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())

