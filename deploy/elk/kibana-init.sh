#!/bin/sh
# Kibana 8 shows nothing in Discover until a data view exists, and it does not
# create one for you. Without this the whole pipeline can be working -- hundreds
# of documents indexed -- and the UI still looks completely empty, which is
# indistinguishable from "logging is broken".
set -e
KB="${KIBANA_URL:-http://kibana:5601}"

echo "waiting for kibana..."
until curl -fsS "$KB/api/status" >/dev/null 2>&1; do sleep 3; done

if curl -fsS -H 'kbn-xsrf: true' "$KB/api/data_views" 2>/dev/null | grep -q '"title":"adviser-\*"'; then
  echo "data view already exists"
  exit 0
fi

curl -fsS -X POST "$KB/api/data_views/data_view" \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' \
  -d '{"data_view":{"title":"adviser-*","name":"Adviser logs","timeFieldName":"@timestamp"}}' \
  >/dev/null && echo "created data view adviser-*"

DVID=$(curl -fsS -H 'kbn-xsrf: true' "$KB/api/data_views" | sed -n 's/.*"id":"\([^"]*\)","title":"adviser-\*".*/\1/p' | head -1)
[ -z "$DVID" ] && DVID=$(curl -fsS -H 'kbn-xsrf: true' "$KB/api/data_views" | tr ',' '\n' | grep -m1 '"id"' | cut -d'"' -f4)

# A saved search with useful columns. The raw Discover view is dominated by
# Docker metadata, so the signal we actually added is hard to find without it.
curl -fsS -X POST "$KB/api/saved_objects/search/adviser-requests?overwrite=true" \
  -H 'kbn-xsrf: true' -H 'Content-Type: application/json' -d "{
  \"attributes\": {
    \"title\": \"Adviser — requests\",
    \"description\": \"One row per API request: route, status, duration, correlation id.\",
    \"columns\": [\"level\",\"http_route\",\"http_status\",\"duration_ms\",\"request_id\"],
    \"sort\": [[\"@timestamp\",\"desc\"]],
    \"kibanaSavedObjectMeta\": {\"searchSourceJSON\": \"{\\\"query\\\":{\\\"query\\\":\\\"http_route:*\\\",\\\"language\\\":\\\"kuery\\\"},\\\"filter\\\":[],\\\"indexRefName\\\":\\\"kibanaSavedObjectMeta.searchSourceJSON.index\\\"}\"}
  },
  \"references\": [{\"id\": \"$DVID\", \"name\": \"kibanaSavedObjectMeta.searchSourceJSON.index\", \"type\": \"index-pattern\"}]
}" >/dev/null 2>&1 && echo "created saved search 'Adviser — requests'"

echo "open http://localhost:5601/app/discover"
