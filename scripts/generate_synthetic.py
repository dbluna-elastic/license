#!/usr/bin/env python3
"""Generate a 180-form synthetic TDLR corpus matching the A2 document shape.

Counts and ratios are computed in the Elasticsearch ingest pipeline, not here.
"""

from __future__ import annotations

import hashlib
import json
import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DICT_PATH = ROOT / "elastic" / "canonical-dictionary.json"
OUT_PATH = ROOT / "data" / "generated" / "synthetic-forms.ndjson"

ALWAYS = ["legal_name", "mailing_address", "phone"]  # solid heatmap band
USUALLY = ["email", "city", "state", "zip", "first_name", "last_name"]
OFTEN = ["ssn", "dob", "business_name", "physical_address"]
SOMETIMES = ["driver_license_number", "dba", "fei_ein", "tax_id"]
RARE = ["criminal_history"]
TRANSACTIONAL = ["signature", "application_date", "fee_amount"]
OCCASIONAL_TX = ["payment_method", "notary"]

SECTORS = [
    {
        "program_sector": "Electrical",
        "license_program": "Electrician",
        "prefix": "ELE",
        "pdf_base": "https://www.tdlr.texas.gov/electricians/forms/",
        "unique_pool": [
            "journeyman_license_number",
            "master_license_number",
            "service_area_counties",
            "voltage_class",
            "apprentice_supervision_ratio",
            "continuing_education_hours",
            "exam_site_preference",
        ],
        "titles": [
            "Application for Electrician License",
            "Electrician License Renewal",
            "Request for Duplicate Electrician License",
            "Master Electrician Upgrade Application",
            "Apprentice Electrician Registration",
            "Electrical Contractor Application",
            "Electrical Sign Contractor Application",
            "Continuing Education Provider Application",
        ],
    },
    {
        "program_sector": "Air Conditioning & Refrigeration",
        "license_program": "ACR Contractor",
        "prefix": "ACR",
        "pdf_base": "https://www.tdlr.texas.gov/aircon/forms/",
        "unique_pool": [
            "epa_certification_number",
            "refrigerant_type",
            "tonnage_rating",
            "certified_technician_count",
            "municipal_permit_number",
            "service_vehicle_count",
        ],
        "titles": [
            "ACR Contractor License Application",
            "ACR Technician Registration",
            "ACR License Renewal",
            "Certified Technician Change of Employer",
            "ACR Contractor Insurance Affidavit",
            "Request to Add ACR Endorsement",
        ],
    },
    {
        "program_sector": "Vehicle Storage Facilities",
        "license_program": "VSF Operator",
        "prefix": "VSF",
        "pdf_base": "https://www.tdlr.texas.gov/vsf/forms/",
        "unique_pool": [
            "storage_lot_capacity",
            "fence_height_feet",
            "lighting_standard",
            "wrecker_agreement_number",
            "after_hours_release_phone",
            "impound_notice_method",
            "vehicle_inventory_system",
        ],
        "titles": [
            "Vehicle Storage Facility License Application",
            "VSF License Renewal",
            "VSF Employee Criminal History Disclosure",
            "Notice of Stored Vehicle",
            "VSF Change of Ownership",
            "Incident Report — Vehicle Release",
        ],
    },
    {
        "program_sector": "Towing",
        "license_program": "Tow Operator",
        "prefix": "TOW",
        "pdf_base": "https://www.tdlr.texas.gov/towing/forms/",
        "unique_pool": [
            "wrecker_class",
            "incident_location",
            "vin",
            "license_plate",
            "storage_facility_id",
            "consent_towing_authorization",
            "nonconsent_towing_authority",
            "boom_capacity_tons",
        ],
        "titles": [
            "Tow Operator License Application",
            "Towing Company License Application",
            "Incident Towing Report",
            "Tow Operator Renewal",
            "Nonconsent Tow Authorization",
            "Towing Company Insurance Filing",
        ],
    },
]

ALIAS_VARIANTS = {
    "ssn": [
        "Social Security Number",
        "SSN",
        "SS#",
        "Social Security No.",
        "Social Security Number (SSN)",
    ],
    "dob": ["Date of Birth", "DOB", "Birth Date"],
    "driver_license_number": ["Driver License Number", "DL#", "Texas Driver License"],
    "legal_name": ["Legal Name", "Full Legal Name", "Name of Applicant"],
    "mailing_address": ["Mailing Address", "Street Address"],
    "phone": ["Phone Number", "Telephone", "Primary Phone"],
    "email": ["Email Address", "E-mail"],
    "business_name": ["Business Name", "Name of Business"],
}

UNMATCHED_BY_SECTOR = {
    "Electrical": [
        "Taxpayer Identification (SSN)",
        "person legally responsible for the property",
        "physical location of the business premises",
    ],
    "Air Conditioning & Refrigeration": [
        "applicant social security identifier",
        "EPA Section 608 card number",
    ],
    "Vehicle Storage Facilities": [
        "physical location of the business premises",
        "person legally responsible for the property",
        "Taxpayer Identification (SSN)",
    ],
    "Towing": [
        "applicant social security identifier",
        "place where the vehicle was taken into custody",
    ],
}


def load_dictionary() -> dict[str, dict]:
    data = json.loads(DICT_PATH.read_text())
    return {f["name"]: f for f in data["fields"]}


def pick(rng: random.Random, pool: list[str], p: float) -> list[str]:
    return [name for name in pool if rng.random() < p]


def build_form(
    rng: random.Random,
    dictionary: dict[str, dict],
    sector: dict,
    seq: int,
    *,
    force_new: bool = False,
    force_high_burden: bool = False,
) -> dict:
    form_id = "LIC041" if force_new else f"{sector['prefix']}-{seq:03d}"
    title = (
        "Application for Additional Occupational License — Cross-Program Filing"
        if force_new
        else sector["titles"][seq % len(sector["titles"])]
    )
    if not force_new and seq % 9 == 0:
        title = f"{title} (Amendment)"

    standard = list(ALWAYS)
    if force_high_burden:
        standard += USUALLY + OFTEN + SOMETIMES + RARE
    else:
        standard += pick(rng, USUALLY, 0.92)
        standard += pick(rng, OFTEN, 0.78)
        standard += pick(rng, SOMETIMES, 0.48)
        standard += pick(rng, RARE, 0.28)
    standard += TRANSACTIONAL
    standard += pick(rng, OCCASIONAL_TX, 0.35)
    # De-dupe, preserve order
    seen = set()
    standard = [f for f in standard if not (f in seen or seen.add(f))]

    unique_n = 1 if force_high_burden else rng.randint(2, 6)
    unique = rng.sample(sector["unique_pool"], k=min(unique_n, len(sector["unique_pool"])))

    sensitive = [
        f
        for f in standard
        if dictionary.get(f, {}).get("category") == "sensitive_pii"
    ]
    state_of_record = [
        f for f in standard if dictionary.get(f, {}).get("reuse_tier") == "state_of_record"
    ]
    citizen_known = [
        f for f in standard if dictionary.get(f, {}).get("reuse_tier") == "citizen_known"
    ]
    categories = [
        dictionary[f]["category"] for f in standard if f in dictionary
    ]

    aliases = []
    alias_pairs = []
    for field in standard:
        variants = ALIAS_VARIANTS.get(field)
        if not variants:
            continue
        raw = rng.choice(variants)
        aliases.append({"raw": raw, "canonical": field})
        alias_pairs.append(f"{field} :: {raw}")

    unmatched = []
    if "ssn" not in standard or rng.random() < 0.35:
        unmatched.extend(rng.sample(UNMATCHED_BY_SECTOR[sector["program_sector"]], k=1))
    if rng.random() < 0.4:
        leftover = [
            u
            for u in UNMATCHED_BY_SECTOR[sector["program_sector"]]
            if u not in unmatched
        ]
        if leftover:
            unmatched.append(rng.choice(leftover))

    rev = date(2023, 1, 1) + timedelta(days=rng.randint(0, 900))
    pages = 1 if force_high_burden else rng.choice([1, 2, 2, 3, 4, 6])
    method = rng.choices(
        ["acroform", "text_heuristic", "needs_ocr"],
        weights=[0.62, 0.28, 0.10],
    )[0]
    confidence = {
        "acroform": round(rng.uniform(0.92, 0.99), 2),
        "text_heuristic": round(rng.uniform(0.70, 0.90), 2),
        "needs_ocr": round(rng.uniform(0.20, 0.45), 2),
    }[method]

    payload = {
        "form_id": form_id,
        "form_title": title,
        "program_sector": sector["program_sector"],
        "license_program": sector["license_program"],
        "pdf_url": f"{sector['pdf_base']}{form_id.lower()}.pdf",
        "revision_date": rev.isoformat(),
        "page_count": pages,
        "standard_fields": standard,
        "sensitive_pii_fields": sensitive,
        "unique_program_fields": unique,
        "reuse_tier_state_of_record": state_of_record,
        "reuse_tier_citizen_known": citizen_known,
        "field_category_instances": categories,
        "field_alias_pairs": alias_pairs,
        "field_aliases_observed": aliases,
        "raw_unmatched_labels": "\n".join(unmatched),
        "extraction_method": method,
        "extraction_confidence": confidence,
        "is_new_or_revised": force_new,
    }
    blob = json.dumps(payload, sort_keys=True).encode()
    payload["content_hash"] = hashlib.sha256(blob).hexdigest()
    return payload


def main() -> None:
    rng = random.Random(41)
    dictionary = load_dictionary()
    forms = []
    for sector in SECTORS:
        for seq in range(1, 45):  # 44 * 4 = 176
            high = seq in {2, 7, 11}  # a few almost-entirely-recollection forms
            forms.append(
                build_form(
                    rng, dictionary, sector, seq, force_high_burden=high
                )
            )
    # Form 41: the E2 pitch line — newly published, reintroduces SSN/DOB/DL
    forms.append(
        build_form(
            rng,
            dictionary,
            SECTORS[0],
            41,
            force_new=True,
            force_high_burden=True,
        )
    )
    # Two extra VSF renewals so we land at 180
    forms.append(build_form(rng, dictionary, SECTORS[2], 90, force_high_burden=True))
    forms.append(build_form(rng, dictionary, SECTORS[3], 91))
    assert len(forms) == 179 or len(forms) == 180, len(forms)
    # pad to 180 if needed
    while len(forms) < 180:
        forms.append(build_form(rng, dictionary, SECTORS[1], 80 + len(forms)))
    forms = forms[:180]
    # Embed a residual subset so C2 can run without ELSER-ing the whole corpus.
    for doc in forms:
        if doc["form_id"] in {"LIC041", "ELE-002", "VSF-007", "TOW-011"} or doc["form_id"].endswith("-003"):
            if doc.get("raw_unmatched_labels"):
                doc["raw_unmatched_labels_semantic"] = doc["raw_unmatched_labels"]

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUT_PATH.open("w") as fh:
        for doc in forms:
            fh.write(json.dumps(doc) + "\n")
    print(f"wrote {len(forms)} docs to {OUT_PATH}")


if __name__ == "__main__":
    main()
