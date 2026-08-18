#!/usr/bin/env python3
"""Deploy dashboards, the E2 alert, Agent Builder tools, and the Form Burden Analyst."""

from __future__ import annotations

import json
import os
import ssl
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
        raise SystemExit(f"Missing {name}")
    return value


class Kibana:
    def __init__(self) -> None:
        self.base = env("KIBANA_URL").rstrip("/")
        self.key = env("KIBANA_API_KEY")
        self.space = os.environ.get("KIBANA_SPACE_ID", "tdlr-form-burden")
        self.ctx = ssl.create_default_context()

    def path(self, p: str) -> str:
        if self.space and self.space != "default":
            return f"/s/{self.space}{p}"
        return p

    def request(self, method: str, path: str, body: object | None = None, extra: dict | None = None) -> tuple[int, object]:
        headers = {
            "Authorization": f"ApiKey {self.key}",
            "Content-Type": "application/json",
            "kbn-xsrf": "true",
            "Elastic-Api-Version": "2023-10-31",
        }
        if extra:
            headers.update(extra)
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, context=self.ctx, timeout=90) as resp:
                raw = resp.read()
                return resp.status, json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                payload = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                payload = {"error": raw.decode(errors="replace")}
            return exc.code, payload


def upsert_dashboards(kb: Kibana) -> None:
    dash_dir = ROOT / "kibana" / "dashboards"
    for path in sorted(dash_dir.glob("*.json")):
        definition = json.loads(path.read_text())
        dash_id = path.stem
        status, body = kb.request(
            "PUT", kb.path(f"/api/dashboards/dashboard/{dash_id}"), definition
        )
        if status in {200, 201}:
            print(f"dashboard {dash_id} upserted ({status})")
            continue
        # Older/newer path variants
        status2, body2 = kb.request("PUT", kb.path(f"/api/dashboards/{dash_id}"), definition)
        if status2 in {200, 201}:
            print(f"dashboard {dash_id} upserted via /api/dashboards ({status2})")
            continue
        print(f"dashboard {dash_id} FAILED {status} {json.dumps(body)[:500]}")
        print(f"  alt {status2} {json.dumps(body2)[:400]}")


def create_alert(kb: Kibana) -> None:
    status, types = kb.request("GET", kb.path("/api/alerting/rule_types"))
    if status != 200:
        print("rule_types failed", status, types)
        return
    es_query = next((t for t in types if t.get("id") == ".es-query"), None)
    if not es_query:
        print("no .es-query rule type; available:", [t.get("id") for t in types][:20])
        return
    print("es-query consumers", es_query.get("authorizedConsumers") or list((es_query.get("enabledInLicense") and []) or []))
    params_schema = es_query.get("params") or {}
    print("es-query param keys", list(params_schema.keys())[:20] if isinstance(params_schema, dict) else type(params_schema))

    rule = {
        "name": "Sensitive field reintroduced on a new or revised form",
        "tags": ["tdlr", "privacy", "form-burden"],
        "rule_type_id": ".es-query",
        "consumer": "stackAlerts",
        "schedule": {"interval": "5m"},
        "enabled": True,
        "params": {
            "searchType": "esqlQuery",
            "timeField": "ingested_at",
            "esqlQuery": {
                "esql": (
                    "FROM tdlr-forms\n"
                    "| WHERE ingested_at <= NOW() AND ingested_at > NOW() - 24 hours\n"
                    "| WHERE is_new_or_revised == true\n"
                    "| WHERE MV_CONTAINS(sensitive_pii_fields, \"ssn\")\n"
                    "    OR MV_CONTAINS(sensitive_pii_fields, \"dob\")\n"
                    "    OR MV_CONTAINS(sensitive_pii_fields, \"driver_license_number\")\n"
                    "    OR MV_CONTAINS(sensitive_pii_fields, \"criminal_history\")\n"
                    "| KEEP form_id, form_title, program_sector, sensitive_pii_fields, pdf_url, ingested_at"
                )
            },
            "timeWindowSize": 24,
            "timeWindowUnit": "h",
            "size": 100,
            "aggType": "count",
            "groupBy": "all",
            "termSize": 5,
            "excludeHitsFromPreviousRun": False,
            "sourceFields": [],
            "threshold": [0],
            "thresholdComparator": ">",
        },
        "actions": [],
    }
    status, body = kb.request("GET", kb.path("/api/alerting/rule/tdlr-e2-sensitive-reintroduction"))
    method = "PUT" if status == 200 else "POST"
    path = kb.path("/api/alerting/rule/tdlr-e2-sensitive-reintroduction")
    if method == "PUT":
        # immutable fields cannot be sent
        update = {
            "name": rule["name"],
            "tags": rule["tags"],
            "schedule": rule["schedule"],
            "params": rule["params"],
            "actions": [],
        }
        status, body = kb.request("PUT", path, update)
    else:
        status, body = kb.request("POST", path, rule)
    if status in {200, 201}:
        print(f"alert E2 {method} ok")
        return
    print(f"alert E2 ES|QL failed {status} {json.dumps(body)[:500]}")
    # Fallback: Query DSL so the rule still exists for the demo story.
    rule["params"] = {
        "searchType": "esQuery",
        "index": ["tdlr-forms"],
        "timeField": "ingested_at",
        "esQuery": json.dumps(
            {
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"is_new_or_revised": True}},
                            {
                                "terms": {
                                    "sensitive_pii_fields": [
                                        "ssn",
                                        "dob",
                                        "driver_license_number",
                                        "criminal_history",
                                    ]
                                }
                            },
                        ]
                    }
                }
            }
        ),
        "timeWindowSize": 24,
        "timeWindowUnit": "h",
        "size": 100,
        "threshold": [0],
        "thresholdComparator": ">",
        "aggType": "count",
        "groupBy": "all",
        "termSize": 5,
        "excludeHitsFromPreviousRun": False,
        "sourceFields": [],
    }
    status, body = kb.request("POST", path, rule)
    if status in {200, 201}:
        print("alert E2 created via Query DSL fallback")
    else:
        print(f"alert E2 DSL failed {status} {json.dumps(body)[:800]}")


TOOLS = [
    {
        "id": "tdlr.get_forms_requesting_field",
        "type": "esql",
        "description": (
            "List TDLR forms that request a specific canonical field. "
            "Use for questions like 'which forms ask for SSN / Social Security / date of birth / driver license'. "
            "field_name must be a canonical name: ssn, dob, driver_license_number, criminal_history, "
            "legal_name, mailing_address, phone, email, business_name."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                "| WHERE MV_CONTAINS(standard_fields, ?field_name) OR MV_CONTAINS(sensitive_pii_fields, ?field_name) "
                "| KEEP form_id, form_title, program_sector, license_program, sensitive_pii_count, pdf_url "
                "| SORT form_id "
                "| LIMIT 50"
            ),
            "params": {
                "field_name": {
                    "type": "string",
                    "description": "Canonical field name such as ssn, dob, driver_license_number, legal_name, phone",
                }
            },
        },
    },
    {
        "id": "tdlr.get_lowest_value_forms",
        "type": "esql",
        "description": (
            "Rank forms with the lowest unique_field_ratio — forms that are almost entirely re-collection. "
            "Pass sector as 'all' to include every program office, or a sector name: "
            "Electrical, Air Conditioning & Refrigeration, Vehicle Storage Facilities, Towing."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                '| WHERE ?sector == "all" OR program_sector == ?sector '
                "| SORT unique_field_ratio ASC, citizen_burden_score DESC "
                "| LIMIT 15 "
                "| KEEP form_id, form_title, program_sector, unique_field_ratio, standard_field_count, unique_field_count, citizen_burden_score"
            ),
            "params": {
                "sector": {
                    "type": "string",
                    "description": "Program sector or the literal value all",
                }
            },
        },
    },
    {
        "id": "tdlr.get_field_variants",
        "type": "esql",
        "description": (
            "Show every observed spelling of a canonical field across the corpus. "
            "This is the 'how many ways does TDLR spell Social Security Number' tool. "
            "canonical_field examples: ssn, dob, phone, legal_name, mailing_address."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                "| MV_EXPAND field_alias_pairs "
                '| DISSECT field_alias_pairs "%{canonical} :: %{raw}" '
                "| WHERE canonical == ?canonical_field "
                "| STATS forms = COUNT_DISTINCT(form_id) BY raw "
                "| SORT forms DESC"
            ),
            "params": {
                "canonical_field": {
                    "type": "string",
                    "description": "Canonical field name, e.g. ssn",
                }
            },
        },
    },
    {
        "id": "tdlr.compare_sectors",
        "type": "esql",
        "description": (
            "Compare the four TDLR program offices on form count, average citizen_burden_score, "
            "average unique_field_ratio, and total redundant field entries. "
            "Use when asked which program office to fix first."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                "| STATS forms = COUNT(*), avg_burden = AVG(citizen_burden_score), "
                "avg_unique_ratio = AVG(unique_field_ratio), redundant_entries = SUM(standard_field_count) "
                "BY program_sector "
                "| SORT avg_burden DESC"
            ),
            "params": {},
        },
    },
    {
        "id": "tdlr.get_form_detail",
        "type": "esql",
        "description": (
            "Return the full scored inventory for one form_id (e.g. LIC041, ELE-002, VSF-007). "
            "Use when the user names a specific form."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                "| WHERE form_id == ?form_id "
                "| KEEP form_id, form_title, program_sector, license_program, pdf_url, page_count, "
                "standard_fields, unique_program_fields, sensitive_pii_fields, "
                "citizen_burden_score, unique_field_ratio, field_alias_pairs, raw_unmatched_labels, is_new_or_revised"
            ),
            "params": {
                "form_id": {
                    "type": "string",
                    "description": "Exact form_id such as LIC041 or ELE-002",
                }
            },
        },
    },
    {
        "id": "tdlr.get_sensitive_exposure_summary",
        "type": "esql",
        "description": (
            "Corpus-level privacy exposure: how many forms request SSN, DOB, driver license, "
            "criminal history, and the SSN+DOB+DL identity-theft trifecta."
        ),
        "configuration": {
            "query": (
                "FROM tdlr-forms "
                "| STATS total_forms = COUNT(*), "
                'ssn_forms = COUNT(*) WHERE MV_CONTAINS(sensitive_pii_fields, "ssn"), '
                'dob_forms = COUNT(*) WHERE MV_CONTAINS(sensitive_pii_fields, "dob"), '
                'dl_forms = COUNT(*) WHERE MV_CONTAINS(sensitive_pii_fields, "driver_license_number"), '
                'criminal_forms = COUNT(*) WHERE MV_CONTAINS(sensitive_pii_fields, "criminal_history"), '
                'trifecta_forms = COUNT(*) WHERE MV_CONTAINS(sensitive_pii_fields, "ssn") AND MV_CONTAINS(sensitive_pii_fields, "dob") AND MV_CONTAINS(sensitive_pii_fields, "driver_license_number")'
            ),
            "params": {},
        },
    },
    {
        "id": "tdlr.search_forms",
        "type": "index_search",
        "description": "Full-text search over TDLR form titles, sectors, and unmatched field labels when a structured tool does not fit.",
        "configuration": {"pattern": "tdlr-forms"},
    },
]

AGENT_INSTRUCTIONS = """You are the Form Burden Analyst for the Texas Department of Licensing and Regulation.

You only answer from tool results. Cite specific form_id values. Never invent a form number, sector, or count. If the tools do not support the question, say so.

Canonical fields you know: ssn, dob, driver_license_number, criminal_history, legal_name, mailing_address, phone, email, business_name.

When asked how TDLR spells a field, call tdlr.get_field_variants. When asked which office to fix first, call tdlr.compare_sectors and pick the highest avg_burden. When asked about prefill impact, use tdlr.get_forms_requesting_field for name/address/phone and reason from the counts — do not fabricate an annual-volume number.

Treat reuse_tier state-of-record fields as data TDLR already stores on the licensee record. Every duplicate collection point is a duplicate retention obligation and a duplicate breach surface.
"""


def upsert_tools(kb: Kibana) -> list[str]:
    ids = []
    for tool in TOOLS:
        tid = tool["id"]
        status, _ = kb.request("GET", kb.path(f"/api/agent_builder/tools/{tid}"))
        if status == 200:
            update = {"description": tool["description"], "configuration": tool["configuration"]}
            status, body = kb.request("PUT", kb.path(f"/api/agent_builder/tools/{tid}"), update)
        else:
            status, body = kb.request("POST", kb.path("/api/agent_builder/tools"), tool)
        if status in {200, 201}:
            print(f"tool {tid} ok")
            ids.append(tid)
        else:
            print(f"tool {tid} FAILED {status} {json.dumps(body)[:500]}")
    return ids


def upsert_agent(kb: Kibana, tool_ids: list[str]) -> None:
    payload = {
        "id": "form-burden-analyst",
        "name": "Form Burden Analyst",
        "description": "Cites TDLR form IDs and never speculates. Grounds every answer in the scored form corpus.",
        "configuration": {
            "instructions": AGENT_INSTRUCTIONS,
            "tools": [{"tool_ids": tool_ids + ["platform.core.search"]}],
        },
    }
    status, _ = kb.request("GET", kb.path("/api/agent_builder/agents/form-burden-analyst"))
    if status == 200:
        status, body = kb.request(
            "PUT",
            kb.path("/api/agent_builder/agents/form-burden-analyst"),
            {
                "description": payload["description"],
                "configuration": payload["configuration"],
            },
        )
    else:
        status, body = kb.request("POST", kb.path("/api/agent_builder/agents"), payload)
    if status in {200, 201}:
        print("agent form-burden-analyst ok")
    else:
        print(f"agent FAILED {status} {json.dumps(body)[:800]}")


def main() -> None:
    load_dotenv()
    kb = Kibana()
    upsert_dashboards(kb)
    create_alert(kb)
    tool_ids = upsert_tools(kb)
    if tool_ids:
        upsert_agent(kb, tool_ids)
    print("Kibana deploy finished")
    print(
        f"Open space: {kb.base}/s/{kb.space}/app/dashboards"
    )


if __name__ == "__main__":
    main()
