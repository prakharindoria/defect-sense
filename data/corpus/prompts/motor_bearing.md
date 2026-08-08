# Corpus generation prompt — motor_bearing

Paste everything inside the fence below into a capable model (GPT-5.x, Claude,
Gemini Pro). Save the reply as `data/corpus/raw/motor_bearing.jsonl`, then run:

```bash
python -m data.generators.corpus_ingest data/corpus/raw/motor_bearing.jsonl
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
component_id:   motor_bearing
component:      Electric motor drive-end rolling element bearing, 6208-2RS,
                shaft speed 1480 rpm, continuous duty
station:        MB-03
inspection:     vibration acceleration timeseries (spectral analysis) and
                bearing housing temperature trend; visual check of seal condition
                and lubricant colour
defect_classes: bearing_wear, lubrication_degraded, misalignment,
                contamination_ingress, looseness, cage_damage
standards:      ISO 20816 (mechanical vibration - evaluation of machine
                vibration), ISO 281 (rolling bearings - dynamic load ratings and
                rating life), ISO 15243 (rolling bearings - damage and failures)

### CAUSAL GRAPH  (every incident MUST instantiate one of these chains)
B1  lubrication_interval_exceeded -> lubricant_film_breakdown ->
    friction_increase -> housing_temperature_rise + broadband_vibration_rise
B2  operating_hours_high -> raceway_fatigue -> bearing_wear ->
    outer_race_fault_frequency_harmonics
B3  coupling_misalignment -> radial_load_asymmetry -> misalignment ->
    twice_running_frequency_energy
B4  seal_degradation -> contamination_ingress -> abrasive_wear ->
    kurtosis_rise_before_rms_rise
B5  shift_C_night -> missed_lubrication_round -> lubrication_degraded
    (deliberate bias hook: night shift MUST be over-represented among missed
     preventive maintenance, so a fairness audit has something real to find)

### HOLDOUT — NEVER MENTION
Do not reference electrical_fluting anywhere, in any record, in any wording. It is
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
   At least 18 must instantiate B1. At least 8 must instantiate B5 with
   shift "C". At least 10 must be incipient (kurtosis up, RMS still normal).

SCHEMA — knowledge
{"type":"knowledge",
 "doc_id":"SOP-MB-114",
 "component_id":"motor_bearing",
 "doc_type":"sop",
 "title":"Short descriptive title",
 "defect_classes":["one_or_more_from_the_list_above"],
 "station":"MB-03",
 "effective_date":"2025-03-01",
 "version":"3.1",
 "standard_ref":null,
 "sections":[
   {"chunk_id":"SOP-MB-114#4.2",
     "heading":"4.2 Short section heading",
     "text":"180-400 words of specific, procedural prose."}
 ]}

SCHEMA — incident
{"type":"incident",
 "doc_id":"INC-2025-0412",
 "component_id":"motor_bearing",
 "occurred_at":"2025-04-12T06:20:00Z",
 "station":"MB-03",
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
   Kurtosis rises BEFORE RMS on incipient faults -- an early-stage incident must
   show elevated kurtosis with near-nominal RMS. Temperature and broadband
   vibration rise together for lubrication faults. Misalignment shows energy at
   twice running frequency (about 49.3 Hz at 1480 rpm), not at the bearing fault
   frequencies. Do not report a fault frequency that contradicts the cause.

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

9. NEVER mention electrical_fluting.
````
