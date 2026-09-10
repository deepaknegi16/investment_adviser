# Observability tier

Opt-in, because Elasticsearch and Kibana together want roughly 2–3 GB while the
whole application stack runs in about 540 MB. Keeping it behind a compose
profile means the default `docker compose up` stays light and the app is never
waiting on a log pipeline to start.

```bash
docker compose --profile observability up -d      # adds ELK
docker compose up -d                              # app only, unchanged
```

| Service | Port | Purpose |
|---|---|---|
| elasticsearch | 9200 | Log index, single node, security disabled (local only) |
| kibana | 5601 | Search and dashboards — http://localhost:5601 |
| filebeat | — | Ships container stdout, parsing the backend's JSON into fields |

## What to look at first

The backend logs one JSON object per request with `request_id`, `http_route`,
`http_status` and `duration_ms`. In Kibana, create a data view on `adviser-*`
with `@timestamp` as the time field, then:

- `http_status >= 500` — real errors
- `duration_ms > 5000` — the AI paths, which legitimately run long
- `request_id: "<id>"` — every log line for one request, including anything the
  agents logged while handling it

## Metrics are separate

`/metrics` on the backend is Prometheus exposition — counters and latency
histograms rather than log lines. It is deliberately not in Elasticsearch:
counting requests by parsing logs is both slower and less accurate than a
counter that was incremented at the point the thing happened.

The counters exist because of specific incidents in this codebase:

| Metric | The incident it would have caught |
|---|---|
| `adviser_yahoo_fetch_total{outcome="missing_from_batch"}` | Symbols silently dropped from a batch, twice — surfaced only as "no data available" in the UI |
| `adviser_agent_runs_total{outcome="quota_exhausted"}` | An exhausted search quota and a broken agent produced identical output |
| `adviser_cache_total{result}` | Whether the shared cache is doing anything, or every worker is refetching |
| `adviser_guardrail_violations_total{kind}` | Which guardrail fired, and how often |
| `adviser_gold_alerts_total{outcome}` | Whether a quiet inbox means nothing happened or the limiter suppressed it |
