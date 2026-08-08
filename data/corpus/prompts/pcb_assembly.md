# Corpus generation prompt — pcb_assembly

Paste everything inside the fence below into a capable model (GPT-5.x, Claude,
Gemini Pro). Save the reply as `data/corpus/raw/pcb_assembly.jsonl`, then run:

```bash
python -m data.generators.corpus_ingest data/corpus/raw/pcb_assembly.jsonl
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
component_id:   pcb_assembly
component:      Surface-mount PCB assembly, 0402 and 0603 passives plus QFN
                packages, lead-free SAC305 reflow
station:        PA-07
inspection:     automated optical inspection of solder joints and component
                placement; solder paste volume from the printer; reflow oven zone
                temperature profile
defect_classes: insufficient_solder, cold_joint, tombstoning,
                component_offset, bridging, missing_component, solder_void
standards:      IPC-A-610 (acceptability of electronic assemblies),
                IPC J-STD-001 (requirements for soldered electrical and electronic
                assemblies), IPC-7095 (design and assembly process implementation
                for BGAs)

### CAUSAL GRAPH  (every incident MUST instantiate one of these chains)
P1  stencil_aperture_clogged -> paste_volume_low ->
    insufficient_solder -> open_joint
P2  reflow_peak_temperature_low -> incomplete_wetting -> cold_joint
P3  placement_nozzle_wear -> component_offset -> tombstoning
P4  paste_volume_high + fine_pitch -> bridging -> short_circuit
P5  shift_C_night -> delayed_stencil_clean -> paste_volume_low
    (deliberate bias hook: night shift MUST be over-represented among missed
     stencil cleaning intervals, so a fairness audit has something real to find)

### HOLDOUT — NEVER MENTION
Do not reference head_in_pillow anywhere, in any record, in any wording. It is
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
   At least 18 must instantiate P1. At least 8 must instantiate P5 with
   shift "C". Because this component is vision-dominant, at least 40 of the 60
   must have been found by optical inspection rather than by a sensor reading.

SCHEMA — knowledge
{"type":"knowledge",
 "doc_id":"SOP-PA-114",
 "component_id":"pcb_assembly",
 "doc_type":"sop",
 "title":"Short descriptive title",
 "defect_classes":["one_or_more_from_the_list_above"],
 "station":"PA-07",
 "effective_date":"2025-03-01",
 "version":"3.1",
 "standard_ref":null,
 "sections":[
   {"chunk_id":"SOP-PA-114#4.2",
     "heading":"4.2 Short section heading",
     "text":"180-400 words of specific, procedural prose."}
 ]}

SCHEMA — incident
{"type":"incident",
 "doc_id":"INC-2025-0412",
 "component_id":"pcb_assembly",
 "occurred_at":"2025-04-12T06:20:00Z",
 "station":"PA-07",
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
   This component is VISION-DOMINANT: most defects are found optically, and
   sensor evidence is thin. Paste volume is reported as a percentage of nominal
   aperture volume; insufficient_solder incidents must show volume BELOW 70%.
   Cold joints must show a reflow peak below 235 C for SAC305. Tombstoning must
   involve a passive of 0402 or 0603 size, never a QFN.

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

9. NEVER mention head_in_pillow.
````
