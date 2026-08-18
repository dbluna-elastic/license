#!/usr/bin/env python3
"""Provision the TDLR Form Burden demo on an Elastic Cloud deployment.

Reads KIBANA_URL, ELASTICSEARCH_URL, ELASTICSEARCH_API_KEY, KIBANA_API_KEY,
and KIBANA_SPACE_ID from the environment (or a local .env file).
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SystemExit(f"Missing required environment variable: {name}")
    return value


class Rest:
    def __init__(self, base: str, api_key: str) -> None:
        self.base = base.rstrip("/")
        self.api_key = api_key
        self.ctx = ssl.create_default_context()

    def request(
        self,
        method: str,
        path: str,
        body: object | None = None,
        *,
        extra_headers: dict | None = None,
        timeout: int = 120,
    ) -> tuple[int, object]:
        url = f"{self.base}{path}"
        data = None
        headers = {
            "Authorization": f"ApiKey {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if extra_headers:
            headers.update(extra_headers)
        if body is not None:
            data = json.dumps(body).encode() if not isinstance(body, (bytes, str)) else (
                body.encode() if isinstance(body, str) else body
            )
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=timeout) as resp:
                raw = resp.read()
                payload = json.loads(raw) if raw else {}
                return resp.status, payload
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else {"error": raw.decode()}
            except json.JSONDecodeError:
                payload = {"error": raw.decode(errors="replace")}
            return exc.code, payload


def put_ok(status: int) -> bool:
    return status in {200, 201}


def ensure_space(kb: Rest, space_id: str) -> None:
    status, body = kb.request("GET", f"/api/spaces/space/{space_id}", extra_headers={"kbn-xsrf": "true"})
    if status == 200:
        print(f"space {space_id} already exists")
        return
    status, body = kb.request(
        "POST",
        "/api/spaces/space",
        {
            "id": space_id,
            "name": "TDLR Form Burden",
            "description": "You already know my name. — TDLR occupational form redundancy demo.",
            "color": "#1D3557",
            "initials": "TF",
            "disabledFeatures": [],
        },
        extra_headers={"kbn-xsrf": "true"},
    )
    if not put_ok(status):
        raise SystemExit(f"Failed to create space: {status} {body}")
    print(f"created space {space_id}")


def put_pipeline(es: Rest) -> None:
    body = json.loads((ROOT / "elastic" / "ingest-pipeline.json").read_text())
    status, resp = es.request("PUT", "/_ingest/pipeline/tdlr-forms-score", body)
    if not put_ok(status):
        raise SystemExit(f"pipeline failed: {status} {resp}")
    print("put ingest pipeline tdlr-forms-score")


def put_templates(es: Rest) -> None:
    for name, filename in (
        ("tdlr-forms", "index-template.json"),
        ("tdlr-forms-failed", "failed-index-template.json"),
    ):
        body = json.loads((ROOT / "elastic" / filename).read_text())
        status, resp = es.request("PUT", f"/_index_template/{name}", body)
        if not put_ok(status):
            raise SystemExit(f"template {name} failed: {status} {resp}")
        print(f"put index template {name}")


def recreate_index(es: Rest, name: str) -> None:
    status, _ = es.request("HEAD", f"/{name}")
    if status == 200:
        del_status, resp = es.request("DELETE", f"/{name}")
        if not put_ok(del_status):
            raise SystemExit(f"delete {name} failed: {del_status} {resp}")
        print(f"deleted existing index {name}")
    status, resp = es.request("PUT", f"/{name}")
    if not put_ok(status):
        raise SystemExit(f"create {name} failed: {status} {resp}")
    print(f"created index {name}")


def bulk_ingest(es: Rest, ndjson_path: Path) -> None:
    docs = [json.loads(line) for line in ndjson_path.read_text().splitlines() if line.strip()]
    fast = [d for d in docs if "raw_unmatched_labels_semantic" not in d]
    slow = [d for d in docs if "raw_unmatched_labels_semantic" in d]
    docs = fast + slow
    print(f"ingest plan: {len(fast)} plain + {len(slow)} semantic")
    batch_size = 25 if not any("raw_unmatched_labels_semantic" in d for d in docs[:1]) else 5
    indexed = 0
    errors = 0
    for i in range(0, len(docs), batch_size):
        batch = docs[i : i + batch_size]
        lines = []
        for doc in batch:
            lines.append(json.dumps({"index": {"_index": "tdlr-forms", "_id": doc["form_id"]}}))
            lines.append(json.dumps(doc))
        payload = "\n".join(lines) + "\n"
        last_err = None
        for attempt in range(1, 5):
            try:
                status, resp = es.request(
                    "POST",
                    "/_bulk?refresh=false",
                    payload,
                    extra_headers={"Content-Type": "application/x-ndjson"},
                    timeout=300,
                )
                if status != 200:
                    last_err = f"{status} {resp}"
                    time.sleep(attempt * 3)
                    continue
                if resp.get("errors"):
                    for item in resp.get("items", []):
                        err = (item.get("index") or {}).get("error")
                        if err:
                            errors += 1
                            if errors <= 8:
                                print("bulk item error:", err)
                break
            except Exception as exc:  # timeout / reset during ELSER warmup
                last_err = str(exc)
                print(f"batch {i} attempt {attempt} failed: {exc}")
                time.sleep(attempt * 4)
        else:
            raise SystemExit(f"bulk failed at offset {i}: {last_err}")
        indexed += len(batch)
        print(f"ingested {indexed}/{len(docs)}")
        time.sleep(0.15)
    es.request("POST", "/tdlr-forms/_refresh")
    print(f"bulk complete: {len(docs)} docs, item_errors={errors}")


def seed_failure(es: Rest) -> None:
    """Deliberately break a doc so on_failure routes it to tdlr-forms-failed."""
    status, resp = es.request(
        "PUT",
        "/tdlr-forms/_doc/FAIL-SEED?pipeline=tdlr-forms-score",
        {"form_id": "FAIL-SEED", "standard_fields": "not-an-array"},
    )
    print(f"failure seed status={status}")
    time.sleep(0.5)
    status, resp = es.request("GET", "/tdlr-forms-failed/_search?size=1")
    hits = ((resp.get("hits") or {}).get("hits") or []) if isinstance(resp, dict) else []
    print(f"tdlr-forms-failed hits={len(hits)}")


def ensure_data_view(kb: Rest, space_id: str) -> None:
    path = f"/s/{space_id}/api/data_views/data_view/tdlr-forms"
    status, _ = kb.request("GET", path, extra_headers={"kbn-xsrf": "true"})
    if status == 200:
        print("data view tdlr-forms already exists")
        return
    status, body = kb.request(
        "POST",
        f"/s/{space_id}/api/data_views/data_view",
        {
            "data_view": {
                "id": "tdlr-forms",
                "title": "tdlr-forms",
                "name": "TDLR Forms",
                "timeFieldName": "ingested_at",
                "allowNoIndex": True,
            },
            "override": True,
        },
        extra_headers={"kbn-xsrf": "true"},
    )
    if not put_ok(status):
        raise SystemExit(f"data view failed: {status} {body}")
    print("created data view tdlr-forms")


def sample_agg(es: Rest) -> None:
    status, resp = es.request(
        "POST",
        "/tdlr-forms/_search",
        {
            "size": 0,
            "aggs": {
                "fields": {"terms": {"field": "standard_fields", "size": 8}},
                "sectors": {"terms": {"field": "program_sector", "size": 10}},
            },
        },
    )
    if status != 200:
        raise SystemExit(f"sample agg failed: {status} {resp}")
    print("standard_fields terms:", [
        (b["key"], b["doc_count"])
        for b in resp["aggregations"]["fields"]["buckets"]
    ])
    print("sectors:", [
        (b["key"], b["doc_count"])
        for b in resp["aggregations"]["sectors"]["buckets"]
    ])


def main() -> None:
    load_dotenv()
    es = Rest(env("ELASTICSEARCH_URL"), env("ELASTICSEARCH_API_KEY"))
    kb = Rest(env("KIBANA_URL"), env("KIBANA_API_KEY"))
    space_id = os.environ.get("KIBANA_SPACE_ID", "tdlr-form-burden")

    status, info = es.request("GET", "/")
    if status != 200:
        raise SystemExit(f"Elasticsearch auth failed: {status} {info}")
    print("connected to Elasticsearch", (info.get("version") or {}).get("number"))

    ensure_space(kb, space_id)
    put_pipeline(es)
    put_templates(es)
    recreate_index(es, "tdlr-forms-failed")
    recreate_index(es, "tdlr-forms")

    ndjson = ROOT / "data" / "generated" / "synthetic-forms.ndjson"
    if not ndjson.exists():
        raise SystemExit(f"missing {ndjson}; run scripts/generate_synthetic.py first")
    bulk_ingest(es, ndjson)
    seed_failure(es)
    ensure_data_view(kb, space_id)
    sample_agg(es)
    print("A1/A2/C1/B5 bootstrap complete")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
