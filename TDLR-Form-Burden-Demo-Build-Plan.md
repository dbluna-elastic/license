# TDLR Form Burden Demo — Build Plan

**Working title:** *"You already know my name."*
**Platform:** Elastic 9.x (Serverless or Cloud Hosted)
**Owner:** David Luna, SA — SLED Central
**Status:** Draft v1 — chunk assignments open

---

## 0. Demo Thesis (read this before building anything)

TDLR administers 40+ occupational license programs across a handful of program
sectors. Each sector historically owned its own forms. The result is that a
single business owner — say an electrical contractor who also operates a vehicle
storage facility — supplies the same identity and business data to the same
agency a dozen times, on a dozen PDFs, into a dozen intake paths.

**The demo proves three claims, in this order:**

1. **Measurement:** the redundancy is real and quantifiable at the field level.
2. **Risk:** the redundant fields are disproportionately *sensitive* — SSN, DOB,
   DL#, criminal history — meaning every duplicate form is a duplicate breach
   surface and a duplicate retention obligation.
3. **Action:** Elastic doesn't just report it. It watches for new forms that
   reintroduce the pattern, routes them for review, and lets a non-technical
   program manager ask questions in plain language.

**Anti-goal:** do not let this become "a dashboard of PDF statistics." If a
stakeholder could get the same answer from a spreadsheet an intern built, the
demo failed. The differentiators are §C2 (semantic alias discovery), §E
(alerting), §F (workflows), and §G (Agent Builder).

**Why Elastic and not a Python script:** the script gives you a number once.
Elastic gives you a number that stays true as TDLR publishes new forms —
plus a system that catches the variants your dictionary never anticipated.

---

## 1. Architecture at a Glance

```
  TDLR public site
        │
        ▼
 [B] Acquisition ──► PDF corpus on disk ──► [B3] label extraction
                                                   │
                                                   ▼
                                    [B4] dictionary normalization
                                                   │
                                                   ▼
   ┌──────────────────────── Elasticsearch ────────────────────────┐
   │  [A2] tdlr-forms index                                        │
   │  [C1] ingest pipeline: scoring, ratios, burden index          │
   │  [C2] semantic_text + ELSER: alias discovery on residuals     │
   │  [C3] enrich policy: sector / license-program metadata        │
   └───────────────────────────────────────────────────────────────┘
        │                    │                  │              │
        ▼                    ▼                  ▼              ▼
  [D] Dashboards       [E] Alert rules    [F] Workflows   [G] Agent Builder
```

---

## 2. Chunk Index

Chunks marked **CRITICAL PATH** block the most downstream work — start there.
Chunks marked **PARALLEL** can be built by someone else at the same time.

| ID | Chunk | Depends on | Rough effort | Notes |
|----|-------|-----------|--------------|-------|
| A1 | Cluster provisioning | — | 0.5 day | CRITICAL PATH |
| A2 | Index template & mappings | A1 | 0.5 day | CRITICAL PATH |
| A3 | Inference endpoint (ELSER) | A1 | 0.5 day | PARALLEL |
| B1 | Form inventory crawl | — | 1 day | PARALLEL, no cluster needed |
| B2 | PDF fetch & local corpus | B1 | 0.5 day | |
| B3 | Field extraction | B2 | 1.5 days | Hardest data-eng piece |
| B4 | Canonical field dictionary | — | 1 day | PARALLEL, no cluster needed |
| B5 | Synthetic fallback corpus | B4 | 0.5 day | Insurance policy — build it |
| C1 | Scoring ingest pipeline | A2 | 1 day | |
| C2 | Semantic alias discovery | A3, C1 | 1 day | **Differentiator** |
| C3 | Program metadata enrich policy | A2 | 0.5 day | Optional for MVP |
| D1 | ES\|QL query pack | C1 | 1 day | Feeds D2–D5 and G1 |
| D2 | PII frequency heatmap | D1 | 0.5 day | |
| D3 | Low-unique-value ranking | D1 | 0.5 day | |
| D4 | Privacy exposure donut | D1 | 0.5 day | |
| D5 | Executive burden summary | D1 | 1 day | The slide they screenshot |
| E1 | New/changed form detection | C1 | 0.5 day | |
| E2 | Sensitive-field reintroduction alert | C1 | 0.5 day | **Best alert story** |
| E3 | Extraction quality alert | C1 | 0.5 day | Ops credibility |
| F1 | Scheduled re-crawl workflow | B1–B3, E1 | 1 day | |
| F2 | Remediation routing workflow | E2 | 1 day | **Differentiator** |
| F3 | Alert-triggered enrichment | E1 | 0.5 day | Optional |
| G1 | Agent Builder tool definitions | D1 | 1 day | |
| G2 | "Form Burden Analyst" agent | G1 | 0.5 day | **Differentiator** |
| G3 | Agent demo question script | G2 | 0.5 day | |
| H1 | Regulatory forcing function | — | 0.5 day | PARALLEL, do early |
| H2 | Demo run-of-show | D5, G3 | 0.5 day | |
| H3 | Leave-behind artifact | H2 | 0.5 day | |

**MVP cut line:** A1, A2, B1–B5, C1, D1–D4, E2, H1, H2. Everything else is
upside. If you have one week, that's the week.

---

# Section A — Foundation

## A1. Cluster Provisioning
**Goal:** a working target environment nobody has to rebuild mid-demo.

**Steps**
- Provision Elasticsearch Serverless project (Search or Security flavor) or a
  Cloud Hosted 9.x deployment. Serverless is faster to stand up; Hosted gives
  you more control over ML node sizing for ELSER.
- Create a dedicated space in Kibana: `tdlr-form-burden`. Keep the demo out of
  the default space so a stray dashboard doesn't derail the story.
- Create an API key scoped to the `tdlr-forms*` index pattern for the ingest
  scripts. Do not demo with elastic superuser.
- Confirm Agent Builder and Workflows are enabled/visible in this deployment
  tier before you promise them in a customer meeting. **Verify current
  availability and licensing tier for both — these have moved between preview
  and GA across recent minors.**

**Definition of done:** you can `POST` a test doc and see it in Discover from a
clean browser session.

---

## A2. Index Template & Mappings
**Goal:** a document shape that makes the three headline visualizations trivial
aggregations rather than post-processing.

**Design principle:** one document = one form. Field labels live as arrays of
keywords, not as free text. Aggregations over arrays are what make the heatmap
a two-line Lens config.

**Field inventory to define**

| Field | Type | Purpose |
|-------|------|---------|
| `form_id` | keyword | e.g. `LIC002` |
| `form_title` | text + keyword subfield | display and search |
| `program_sector` | keyword | heatmap axis |
| `license_program` | keyword | finer than sector; from C3 |
| `pdf_url` | keyword (index: false) | click-through from dashboard |
| `revision_date` | date | change detection |
| `content_hash` | keyword | change detection (E1) |
| `page_count` | integer | proxy for burden |
| `total_field_count` | integer | denominator |
| `standard_fields` | keyword (array) | **heatmap rows** |
| `sensitive_pii_fields` | keyword (array) | donut + E2 |
| `unique_program_fields` | keyword (array) | numerator |
| `standard_field_count` | integer | computed in C1 |
| `unique_field_count` | integer | computed in C1 |
| `unique_field_ratio` | float | computed in C1 |
| `reuse_tier_state_of_record` | keyword (array) | the "we already have this" set |
| `sensitive_pii_count` | integer | computed in C1 |
| `citizen_burden_score` | float | computed in C1 |
| `field_aliases_observed` | nested (raw, canonical) | proves the normalization |
| `raw_unmatched_labels` | semantic_text | **C2 hook** |
| `extraction_method` | keyword | `acroform` / `text_heuristic` / `needs_ocr` |
| `extraction_confidence` | float | drives E3 |
| `ingested_at` | date | |

**Decisions to make and record**
- Data stream vs. plain index. Recommend **plain index with `form_id` as `_id`**
  so re-crawls upsert rather than duplicate. Change detection then works off
  `content_hash` diffing, not document count.
- `nested` on `field_aliases_observed` costs you mapping complexity but buys the
  "show me every way TDLR spells Social Security Number" moment. Worth it.

**Definition of done:** index template applied; a hand-written sample doc
indexes without mapping errors; `standard_fields` aggregates correctly in a
terms agg.

---

## A3. Inference Endpoint (ELSER)
**Goal:** semantic capability available before C2 needs it.

**Steps**
- Create an inference endpoint using the ELSER model (or the current default
  `.elser-2-elasticsearch` / EIS-hosted equivalent for your deployment type).
- Wire it to the `raw_unmatched_labels` field via `semantic_text` in A2.
- Warm the model and confirm ML node capacity — first-call latency on a cold
  ELSER deployment is the single most common live-demo stumble. Send a throwaway
  inference request in your pre-demo checklist.

**Definition of done:** a `semantic` query against a test doc returns a hit for
a paraphrase that shares no keywords with the indexed text.

---

# Section B — Data Acquisition

## B1. Form Inventory Crawl
**Goal:** a manifest of every downloadable form with its ID, title, sector, and
URL. This is the spine — get it right and everything downstream is mechanical.

**Steps**
- Identify the TDLR pages that index forms by program. Do **not** hardcode a URL
  pattern; discover PDF links from the program pages and record the discovered
  URL alongside the form ID.
- Parse form ID and title from link text and/or the PDF filename. Expect
  inconsistency — some links are `LIC002`, some are `lic002.pdf`, some are only
  a descriptive title.
- Infer `program_sector` from the alphanumeric prefix (LIC/ENF/MIL, AB/EAB,
  VSF/TOW, ELE/ACR). Capture unmatched prefixes rather than dropping them —
  the long tail is itself a finding.
- Emit `manifest.jsonl`.

**Manners:** it is a public state agency site and these are public forms, but
crawl politely — set a descriptive User-Agent with your contact, rate-limit to
roughly one request per second, honor `robots.txt`, and cache aggressively so
you fetch each PDF once. You may end up demoing this *to* TDLR; you want the
access log to look like a considerate researcher.

**Target:** 150–200 forms across four sectors is a representative sample. You do
not need every form TDLR has ever published.

**Definition of done:** `manifest.jsonl` with ≥150 entries; sector distribution
spot-checked against the live site for at least one program.

---

## B2. PDF Fetch & Local Corpus
**Goal:** an immutable local corpus so nothing in the demo depends on the
network at showtime.

**Steps**
- Download each manifest entry to `data/pdfs/{form_id}.pdf`.
- Record `content_hash` (SHA-256), byte size, and HTTP `Last-Modified`.
- Log failures explicitly — dead links are a finding, not an error to swallow.

**Definition of done:** corpus on disk; failure log reviewed; total size noted.

---

## B3. Field Extraction
**Goal:** convert each PDF into a list of raw field labels. **This is the
highest-risk chunk — budget accordingly.**

**Extraction ladder, stop at first success:**
1. **AcroForm widgets.** Many TDLR forms are fillable. Read the widget
   dictionary and prefer the tooltip (`/TU`) over the field name (`/T`) — `/TU`
   is human-written text, `/T` is frequently `Text17`. This is the highest
   fidelity source available because it's the agency's own declared structure.
2. **Text-layer heuristics.** For flat PDFs: lines ending in a colon, lines
   followed by a fill rule of underscores or dots, and multiple labels per row
   (`First Name: ____  MI: __`). Strip a stoplist of boilerplate — agency
   address, "For Office Use Only", page numbers, revision stamps.
3. **OCR flag.** Image-only PDFs get `extraction_method: needs_ocr` and are
   excluded from ratio math but *counted* in coverage stats. Optionally run
   `ocrmypdf` as a stretch goal.

**Emit per form:** `raw_labels[]`, `extraction_method`, `extraction_confidence`,
`page_count`.

**Definition of done:** ≥80% of the corpus yields ≥4 labels; you have manually
validated extraction against 10 PDFs you opened by hand. **Do this manual
validation.** Every credibility question in the room will be "how do you know
that's right," and "I checked ten by hand" is the answer that lands.

---

## B4. Canonical Field Dictionary
**Goal:** define what "standard/reusable" means, so everything else is
program-specific by subtraction.

**The key inversion:** you never enumerate the long tail of domain fields. You
only define the reusable set. Anything unmatched is unique by definition. This
is what makes the approach scale past TDLR to any agency.

**Structure per canonical field:**
- `name` — e.g. `ssn`
- `variants[]` — every observed spelling
- `category` — `sensitive_pii` / `contact` / `business` / `financial` /
  `administrative`  → drives the donut (D4)
- `reuse_tier` — `state_of_record` (TDLR already has it) / `citizen_known`
  (citizen has it but retypes every time) / `transactional` (legitimately
  per-submission: signature, date, fee)

**`reuse_tier` is the political payload.** "38 forms ask for data TDLR already
stores in the licensee record" is a far sharper sentence than "38 forms ask for
SSN." Do not skip this attribute to save time.

**Matching passes:** exact → token-subset → fuzzy ratio. Guard the token-subset
pass against short variants swallowing longer labels (`state` must not match
`state of incorporation`).

**Definition of done:** dictionary covers the categories in the sector table;
running it against B3 output classifies ≥70% of labels as standard-or-unique
with a residual you're comfortable defending.

---

## B5. Synthetic Fallback Corpus
**Goal:** a demo that runs when the venue wifi doesn't, or when a live re-crawl
returns garbage twenty minutes before the meeting.

**Steps**
- Generate 150–200 realistic form documents matching the A2 shape, with field
  distributions derived from whatever real extraction you have.
- Store as a bulk NDJSON file. One command to load, one command to wipe.

**Do not skip this.** It costs half a day and it is the difference between
"let me reset the demo" and "let me reschedule the meeting."

**Definition of done:** wipe the index, load synthetic, all D dashboards render.

---

# Section C — In-Cluster Enrichment

## C1. Scoring Ingest Pipeline
**Goal:** compute derived metrics *inside Elasticsearch*, not in Python.

**Why this matters for the demo:** when TDLR asks "what happens when we publish
a new form," the answer is "nothing — you drop the PDF in and the pipeline
scores it." If the math lives in your laptop script, that answer is a lie.

**Processors, in order**
1. `script` — `standard_field_count = standard_fields.size()`
2. `script` — `unique_field_count = unique_program_fields.size()`
3. `script` — `total_field_count = standard + unique`
4. `script` — `unique_field_ratio = unique / total` (guard divide-by-zero)
5. `script` — `sensitive_pii_count = sensitive_pii_fields.size()`
6. `script` — **`citizen_burden_score`**: weight `state_of_record` fields
   highest (agency already has it → pure waste), `citizen_known` medium,
   `transactional` at zero. This is your composite ranking metric.
7. `set` — `ingested_at` from `_ingest.timestamp`
8. `on_failure` — route to `tdlr-forms-failed` with the error. Never silently
   drop; failed extractions are themselves a data-quality story.

**Definition of done:** re-index the corpus through the pipeline; verify ratios
by hand on three forms; confirm the failure index catches a deliberately
malformed doc.

---

## C2. Semantic Alias Discovery — **DIFFERENTIATOR**
**Goal:** catch the field variants your dictionary missed, and demonstrate that
the system improves without a code change.

**This is the chunk that makes it an Elastic demo rather than a pandas script.**

**Mechanic**
- `raw_unmatched_labels` (the residual from B4) is mapped as `semantic_text`.
- Run semantic queries for each canonical concept: *"applicant social security
  identifier"*, *"physical location of the business premises"*, *"person legally
  responsible for the property."*
- Surface high-scoring residuals as **candidate aliases** — labels that are
  semantically the same field under a spelling nobody predicted.
- Feed accepted candidates back into the B4 dictionary. Show the residual
  shrinking between run one and run two.

**The demo moment:** "Our dictionary didn't have 'Taxpayer Identification (SSN)'
— we never wrote that variant. Elastic found it anyway. Here's the ratio before
and after." That's a 45-second beat that reframes the entire session.

**Definition of done:** ≥10 genuine aliases discovered that were absent from the
hand-built dictionary; before/after residual counts captured for the script.

---

## C3. Program Metadata Enrich Policy
**Goal:** join each form to license-program context — active licensee counts,
renewal cadence, fee, statutory authority.

**Why:** it converts field counts into **volume-weighted** impact. "This form
has 14 redundant fields" is a statistic. "This form has 14 redundant fields and
is submitted 31,000 times a year" is a budget line.

**Steps**
- Build a small `tdlr-programs` source index (hand-curated is fine — 40 rows).
- Create an enrich policy keyed on `license_program` or sector.
- Add the `enrich` processor to the C1 pipeline.

**Optional for MVP, but this is the chunk that produces the number an executive
repeats to someone else.** If you can get even rough annual submission volumes,
prioritize it up.

**Definition of done:** documents carry program metadata; a
`redundant_fields × annual_submissions` metric renders in D5.

---

# Section D — Dashboards

## D1. ES|QL Query Pack
**Goal:** every visualization and every Agent Builder tool draws from one
reviewed, version-controlled set of queries.

**Build these queries:**
- Field frequency across all forms (`MV_EXPAND standard_fields` → `STATS`)
- Field frequency by sector (the heatmap source)
- Forms ranked by ascending `unique_field_ratio`
- Forms ranked by descending `citizen_burden_score`
- Sensitive-PII form count and percentage of corpus
- Category distribution across all field instances
- Count of distinct forms requesting each `state_of_record` field
- Extraction coverage by method
- Alias variant count per canonical field (the "twelve ways to spell SSN" query)

**`MV_EXPAND` is the workhorse here** — it turns the keyword arrays from A2 into
rows you can aggregate. Get comfortable with it before building D2.

**Definition of done:** all queries reviewed, saved, and committed with a
one-line comment explaining what claim each supports.

---

## D2. PII Frequency Heatmap
**Claim:** *redundancy is universal, not localized to one bad program.*

- Rows: canonical standard fields. Columns: program sector (or license program
  if C3 landed). Cell value: distinct form count.
- Sort rows by total descending so SSN / name / address / phone form a solid
  band across the top.
- **The visual argument is the solid horizontal bar.** If the heatmap looks
  patchy, you've either got a normalization gap in B4 or the redundancy thesis
  is weaker than assumed — investigate before building more.

---

## D3. Low Unique Value Ranking
**Claim:** *some forms are almost entirely re-collection.*

- Horizontal bar, ascending `unique_field_ratio`, top 20.
- Stack each bar: standard fields vs. unique fields, so the reader sees a long
  standard segment and a stub of actual program content.
- Annotate the worst offender by name. Pick one form and know its story cold —
  open the actual PDF during the demo.

---

## D4. Privacy Exposure Donut
**Claim:** *the redundancy is concentrated in exactly the data you least want
duplicated.*

- Segment by field `category`, or by a computed exposure tier.
- Companion metric tiles: count of forms requesting SSN; count requesting SSN
  *and* DOB *and* DL# together (the identity-theft trifecta); count requesting
  criminal history.
- Pair with a callout of retention/breach implications. Every duplicate
  collection point is an independent retention obligation and an independent
  disclosure surface.

---

## D5. Executive Burden Summary
**Claim:** *here is the number, and here is what to do first.*

The dashboard a program director screenshots and forwards. Build it last, from
the findings the others surface.

- Headline metric: total redundant field-entries across the corpus (× annual
  submissions if C3 landed).
- "Top 10 forms to fix first," ranked by `citizen_burden_score`.
- Sector comparison: which program office has the worst ratio.
- Coverage/confidence footer — be transparent about extraction quality. Showing
  your error bars *builds* trust with a technical government audience; hiding
  them gets you caught.

---

# Section E — Alerting

Framing for all of E: this is what makes it a **system** rather than a **study**.
A consultant delivers a PDF of findings that's stale in a quarter. Elastic keeps
the finding true.

## E1. New or Changed Form Detection
- Rule on `content_hash` changes or new `form_id` values appearing.
- Fires on any re-crawl that surfaces a published or revised form.
- Action: notify the forms-governance distribution list with the diff.

## E2. Sensitive-Field Reintroduction — **BEST ALERT STORY**
- **Trigger:** a newly published or revised form requests SSN, DOB, DL#, or
  criminal history *when the licensee record already contains it.*
- This is a policy control, not a metric. It's the difference between reporting
  on the past and governing the future.
- **The pitch line:** "TDLR doesn't need a report on how many forms ask for
  SSN. TDLR needs to know the moment form number 41 does."
- Action: route to the privacy officer with the specific fields and the form URL.

## E3. Extraction Quality Alert
- Fires when `extraction_confidence` drops below threshold or `needs_ocr` count
  spikes — usually meaning TDLR changed their PDF tooling.
- Unglamorous, and it's the one that convinces the engineers in the room that
  you've run something in production before. Keep it.

**Definition of done for E:** each rule fires against a deliberately seeded test
document; connector delivers to a real destination (email, Slack, or webhook).

---

# Section F — Workflows

Workflows turn detection into orchestration. **Confirm current availability and
licensing tier in your target deployment before scoping these into a customer
commitment.**

## F1. Scheduled Re-Crawl
- Weekly trigger → invoke crawl/extract → bulk upsert → summarize delta.
- Demonstrates the pipeline is a living system, not a one-time load.
- The honest architectural note: the crawl and PDF parsing run outside the
  cluster. The workflow orchestrates and ingests. Say this plainly rather than
  implying Elasticsearch is downloading PDFs itself — a technical audience will
  spot the hand-wave and you'll spend the rest of the meeting rebuilding trust.

## F2. Remediation Routing — **DIFFERENTIATOR**
Triggered by E2. Chain:
1. Query the form's full field inventory.
2. Identify which requested fields already exist in the licensee record.
3. Compose a structured review packet: form ID, redundant fields, suggested
   prefill source, affected volume.
4. Create a ticket in the agency's system (ServiceNow / Jira / monday.com) and
   post to the program office channel.

**This is the chunk that moves the conversation from observability spend to
process-improvement spend** — a different, larger, and more durable budget.

## F3. Alert-Triggered Enrichment
- On new form detection, auto-run the C2 semantic alias pass and flag
  never-before-seen field labels for dictionary review.
- The system's vocabulary grows as the agency's forms change. Optional, but a
  strong closer if you have time.

---

# Section G — Agent Builder

**The audience insight:** the person who can actually kill redundant forms is a
program director, not an analyst. That person will never write ES|QL and will
open your dashboard exactly twice. Agent Builder is how the finding survives
past the demo.

## G1. Tool Definitions
Wrap D1 queries as agent tools with tight, unambiguous descriptions:
- `get_forms_requesting_field(field_name)`
- `get_lowest_value_forms(limit, sector)`
- `get_field_variants(canonical_field)` — the alias evidence
- `compare_sectors(metric)`
- `get_form_detail(form_id)`
- `get_sensitive_exposure_summary()`

**Tool descriptions are the actual engineering here.** Vague descriptions
produce an agent that picks the wrong tool and embarrasses you live. Budget real
time for iterating on the description text, and test each tool in isolation
before assembling the agent.

## G2. "Form Burden Analyst" Agent
- Persona: a policy analyst who cites specific form IDs and never speculates.
- System instruction must include: *state when the data doesn't support an
  answer.* An agent that confidently invents a form number in front of a state
  agency ends the opportunity, not just the demo.
- Ground every response in tool output with form IDs the audience can verify by
  opening the PDF.

## G3. Demo Question Script
Rehearse these; verify each returns something good **on the exact corpus you're
demoing**:
- "Which forms ask for Social Security numbers?"
- "What's the single worst form for redundant data entry?"
- "How many different ways does TDLR spell 'Social Security Number'?"
- "If we prefilled name, address, and phone from the licensee record, how many
  field entries would that eliminate?"
- "Which program office should we fix first, and why?"

**The last two are the money questions** — they're the ones a director would
actually ask, and they're the ones that show the agent reasoning over the data
rather than reciting a dashboard. Lead with the SSN spelling question though;
it gets a reaction every time.

---

# Section H — Narrative & Delivery

## H1. Regulatory / Political Forcing Function — **DO THIS EARLY**
Every strong pursuit doc has a reason this matters *now*. Research and pick one:
- TDLR's Sunset Advisory Commission review cycle and any findings on licensing
  process efficiency
- Texas DIR statewide digital-government and data-sharing initiatives
- Texas.gov / TxT consolidated citizen-identity direction
- Occupational licensing reform legislation from recent sessions
- Any TDLR strategic-plan language on customer service or processing times

**Verify current status before citing anything to the customer** — legislative
and sunset timelines shift, and a stale citation in front of a state agency
costs you more credibility than having no citation at all.

Deliverable: one paragraph naming the driver and why the timing is now.

## H2. Demo Run-of-Show
Target 20 minutes of demo inside a 45-minute meeting.

1. **Hook (2 min):** open one real TDLR PDF. Then a second. Point at the same
   six fields. No slides.
2. **Scale (4 min):** D2 heatmap. The solid band does the talking — say less
   than you want to here.
3. **Risk (3 min):** D4 donut plus the SSN+DOB+DL trifecta count.
4. **Credibility (3 min):** C2 alias discovery. Before/after residual.
5. **System (4 min):** E2 alert firing → F2 workflow routing a ticket.
6. **Access (3 min):** Agent Builder, two or three questions from G3.
7. **Close (1 min):** D5 executive summary. Hand them the number.

**Rehearsal note:** steps 1 and 7 are the ones they'll remember. Everything in
between is evidence. If you're running long, compress the middle, never the
bookends.

## H3. Leave-Behind
- Two-page PDF: the headline number, top-10 remediation list, methodology and
  its limits.
- Include the honest coverage caveat. A government audience that finds an
  overclaim on their own turf will discount everything else you said.

---

# Risks & Open Questions

| Risk | Impact | Mitigation |
|------|--------|------------|
| PDFs are image-only, low text yield | Guts B3 | Test extraction on 10 PDFs **before** committing to full scope |
| Redundancy thesis weaker than assumed | Guts the narrative | Validate on 20 forms in week one — if the heatmap is patchy, reframe toward privacy exposure rather than redundancy |
| Agent Builder / Workflows tier or availability mismatch | Cuts F and G | Confirm in A1 before scoping to customer |
| ELSER cold-start latency | Live demo stall | Pre-warm in checklist; have D-section fallback |
| Form volume data unavailable | Weakens C3 and D5 | Public reports, open records request, or clearly-labeled estimates |
| Over-scraping draws attention | Relationship damage | Rate limit, identify yourself, cache |

**Open questions to resolve before build:**
- Do we demo against real scraped data or synthetic? *(Recommend: real data,
  synthetic loaded and tested as fallback.)*
- Is TDLR the actual target account, or is this a reusable pattern to show other
  SLED licensing agencies? *(Changes how much you invest in H1 specificity —
  if it's a pattern demo, keep the forcing function generic and swappable.)*
- Who owns B3 if extraction turns out harder than budgeted?

---

# Suggested First Week

**Day 1:** A1 + B1 (parallel) — cluster up, manifest built
**Day 2:** B2 + B3 start — corpus down, extraction prototyped on 10 forms
**Day 3:** B3 finish + B4 — extraction working, dictionary drafted
**Day 4:** A2 + C1 — mappings and scoring pipeline, first real ratios
**Day 5:** D1 + D2 — query pack and the heatmap

**End of week checkpoint:** if the D2 heatmap shows a solid band across the top,
the thesis holds and you build forward into C2/E/F/G. If it doesn't, stop and
reframe before investing another week.
