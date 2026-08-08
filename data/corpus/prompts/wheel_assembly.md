# Corpus generation prompt — wheel_assembly

Paste everything inside the fence below into a capable model (GPT-5.x, Claude,
Gemini Pro). Save the reply as `data/corpus/raw/wheel_assembly.jsonl`, then run:

```bash
python -m data.generators.corpus_ingest data/corpus/raw/wheel_assembly.jsonl
```

Ingestion **hard-rejects** a corpus that breaks the causal-graph, holdout or
measurement-consistency rules. That is deliberate: a corpus that quietly
contradicts itself would make the demo wrong in a way nobody could see later.

---

````text
You are a manufacturing quality engineer writing the reference documentation and
incident history for ONE component's inspection station. Your output seeds a
retrieval corpus for a quality-control AI, so it must be internally consistent,
specific, and free of invented authority.

Return ONLY JSONL: one JSON object per line. No prose, no markdown fences, no
commentary, no trailing summary. Every line must parse independently as JSON.

═══════════════════════════════════════════════════════════════════════
### COMPONENT
component_id:   wheel_assembly
component:      Automotive wheel hub assembly, 5 fasteners on a 120 mm bolt circle,
                M14x1.5 hub bolts, torque specification 110-125 Nm
station:        WA-01
inspection:     torque-vs-angle sweep per fastener; visual check of fastener
                count, bolt-circle diameter, angular spacing, TPMS presence
defect_classes: thread_contamination, cross_threading, over_torque,
                under_torque, missing_fastener, wrong_wheel_variant,
                hub_seating_gap, missing_tpms, rim_surface_damage
standards:      ISO 16047 (fasteners - torque/clamp force testing),
                ISO 898-1 (mechanical properties of fasteners),
                VDI 2230 (systematic calculation of bolted joints)

### CAUSAL GRAPH  (every incident MUST instantiate one of these chains)
C1  ambient_humidity_high -> thread_surface_condition_degraded ->
    thread_contamination -> clamp_load_deficit
C2  tool_age_cycles_high + days_since_calibration_high ->
    torque_delivery_error -> over_torque OR under_torque
C3  material_lot_hardness_out_of_range -> cross_threading -> stud_damage
C4  fixture_wear -> hub_seating_gap -> uneven_clamp_distribution
C5  shift_C_night -> missed_manual_check -> defect_escapes_to_next_station
    (deliberate bias hook: night shift MUST be over-represented among escaped
     defects, so a fairness audit has something real to find)

### HOLDOUT — NEVER MENTION
Do not reference valve_stem_damage anywhere, in any record, in any wording. It is
reserved as an unseen defect class used to measure whether the system can detect
something it was never taught. Any mention invalidates that measurement.
═══════════════════════════════════════════════════════════════════════

PRODUCE

A) 40 knowledge documents, "type": "knowledge"
   - 14 SOP / work instructions   (doc_type "sop")
   -  8 standards summaries       (doc_type "standard_summary")
   -  8 maintenance procedures    (doc_type "manual")
   -  6 FMEA entries              (doc_type "fmea")
   -  4 limit rationale documents (doc_type "rationale")

B) 60 incidents, "type": "incident"
   Distribute across defect_classes in proportion to how often each actually
   occurs in practice -- common failure modes should dominate, rare ones should
   appear once or twice.
   At least 20 must instantiate C1. At least 8 must instantiate C5 with
   shift "C".

SCHEMA — knowledge
{"type":"knowledge",
 "doc_id":"SOP-WA-114",
 "component_id":"wheel_assembly",
 "doc_type":"sop",
 "title":"Short descriptive title",
 "defect_classes":["one_or_more_from_the_list_above"],
 "station":"WA-01",
 "effective_date":"2025-03-01",
 "version":"3.1",
 "standard_ref":null,
 "sections":[
   {"chunk_id":"SOP-WA-114#4.2",
     "heading":"4.2 Short section heading",
     "text":"180-400 words of specific, procedural prose."}
 ]}

SCHEMA — incident
{"type":"incident",
 "doc_id":"INC-2025-0412",
 "component_id":"wheel_assembly",
 "occurred_at":"2025-04-12T06:20:00Z",
 "station":"WA-01",
 "tool_id":"TOOL-ID-04",
 "shift":"A" | "B" | "C",
 "unit_ref":"UNIT-04412",
 "observed":"What the operator and instruments actually saw, with concrete
             measurements and units.",
 "measurements":{"named_measurement":0.0},
 "initial_hypothesis":"What was suspected first. OFTEN WRONG.",
 "confirmed_cause":"one_defect_class_from_the_list",
 "causal_chain":["node","node","node"],
 "resolution":"What was actually done about it.",
 "outcome":"What happened afterwards, including whether it recurred.",
 "time_to_resolve_hours":6.5,
 "units_affected":14}

RULES

1. SPECIFIC, NOT GENERIC. Every document names real values, tolerances, tool
   IDs and durations. "Check the fastener is tight" is useless. "Verify the
   torque-angle knee occurs between 12 and 16 degrees; a knee beyond 18 degrees
   indicates degraded thread surface condition" is useful. Apply the equivalent
   level of specificity to this component.

2. STANDARDS. You may name a standard by its REAL title and describe its scope
   IN YOUR OWN WORDS. You must NOT invent clause or section numbers, and you
   must NOT reproduce standard text. Put only the standard's name in
   "standard_ref". Every standards summary must begin with exactly:
   "Paraphrased summary, not an extract. Consult the published standard for
   normative requirements."

3. INCIDENTS INSTANTIATE THE GRAPH. "confirmed_cause" must be one of the listed
   defect_classes, and "causal_chain" must be the node sequence of one of the
   chains above, in order. This is scored against automatically, so an invented
   chain silently corrupts the evaluation rather than failing loudly.

4. THE FIRST GUESS IS OFTEN WRONG. In at least 25 of the 60 incidents,
   "initial_hypothesis" must differ from "confirmed_cause". A corpus where the
   first suspicion is always right teaches nothing and makes the system look
   better than it is.

5. MEASUREMENTS MUST BE CONSISTENT WITH THE CAUSE.
   For thread_contamination the final torque stays INSIDE 110-125 Nm while the
   knee angle is LATE (above 18 deg against a ~14 deg baseline) and the elastic
   slope is LOW (below 2.6 against a ~3.2 Nm/deg baseline). That exact
   combination -- in spec, wrong shape -- is the entire reason this system
   exists. For under_torque the final torque is BELOW 110 Nm and the shape is
   normal. For over_torque the curve flattens past yield.

6. VARY THE WRITING. SOPs are terse and numbered. Incidents are narrative and
   read as though different shift engineers wrote them. Maintenance procedures
   are checklists. FMEA entries are tabular prose. Retrieval comparisons only
   show a real difference on a genuinely heterogeneous corpus, so uniform prose
   would quietly undermine the evaluation.

7. TIMELINE. Spread "occurred_at" across 2024-06-01 to 2025-06-01. Cluster some
   incidents realistically -- one bad material lot or one worn tool causes
   several within the same week.

8. NO REAL COMPANIES, PLANTS OR PEOPLE. Operators are opaque tokens like
   OP-7f3a. Do not name a real manufacturer.

9. NEVER mention valve_stem_damage.
````
