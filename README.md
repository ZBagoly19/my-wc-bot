# Owl FC - World Cup Bot

A base bot for the in-company **World Cup Prediction League** for now.

## Layout

```
my-wc-bot/
  predict.py        # entrypoint — reads the match from sys.argv[1], prints the prediction
  requirements.txt  # optional extra Python packages (empty by default)
  meta.yaml         # optional display name + description
```

## Contract

- The framework writes the match as JSON to a file and passes its path as
  `sys.argv[1]` (mounted at `/match/input.json`).
- The **last non-empty line** of stdout must be the prediction:

  ```json
  {"home_goals": 2, "away_goals": 1}
  ```

  Both values are integers in the range `0`–`20`.

## Test it locally

Create a sample input and run the bot — the last printed line is your prediction:

```bash
cat > sample_input.json <<'JSON'
{
  "match_id": 7741,
  "home_team": "Brazil",
  "away_team": "France",
  "stage": "group",
  "kickoff_utc": "2026-06-15T18:00:00Z",
  "api_url": "http://localhost:8000",
  "llm_proxy_url": "https://llm-proxy.corp.local/v1",
  "llm_api_key": "test-token"
}
JSON

python predict.py sample_input.json
```

(The bot falls back to a safe `1-1` if the league API is unreachable, so it works
offline too.)

## Register

1. Push this repo to a Git remote the league can clone.
2. Open the league website → **Register**, log in with your company LDAP
   credentials, and provide the repo URL (entrypoint defaults to `predict.py`).

## Make it your own

Replace the heuristic in `predict_score` inside [`predict.py`](predict.py) with
your own model. Add any packages you need to `requirements.txt`.

