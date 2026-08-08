"""Compose the corpus generation prompts, one complete file per component.

The prompt body is identical across components; only the component block and its
causal graph differ. Keeping the body in one place means a rule fix (say,
tightening the standards-paraphrase instruction) reaches all three prompts
instead of two of them.

    python -m data.corpus.build_prompts

Writes `data/corpus/prompts/<component_id>.md`, each self-contained and ready to
paste into whichever model is generating the corpus.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

OUT_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True, slots=True)
class ComponentSpec:
    component_id: str
    description: str
    station: str
    inspection: str
    defect_classes: str
    standards: str
    causal_graph: str
    holdout: str
    # Rule 5 is component-specific: it names the measurement combination that
    # must stay internally consistent, because getting it wrong in the corpus
    # silently poisons the case the whole product is built around.
    consistency_rule: str
    incident_focus: str
    doc_prefix: str


WHEEL = ComponentSpec(
    component_id="wheel_assembly",
    description=(
        "Automotive wheel hub assembly, 5 fasteners on a 120 mm bolt circle,\n"
        "                M14x1.5 hub bolts, torque specification 110-125 Nm"
    ),
    station="WA-01",
    inspection=(
        "torque-vs-angle sweep per fastener; visual check of fastener\n"
        "                count, bolt-circle diameter, angular spacing, TPMS presence"
    ),
    defect_classes=(
        "thread_contamination, cross_threading, over_torque,\n"
        "                under_torque, missing_fastener, wrong_wheel_variant,\n"
        "                hub_seating_gap, missing_tpms, rim_surface_damage"
    ),
    standards=(
        "ISO 16047 (fasteners - torque/clamp force testing),\n"
        "                ISO 898-1 (mechanical properties of fasteners),\n"
        "                VDI 2230 (systematic calculation of bolted joints)"
    ),
    causal_graph="""C1  ambient_humidity_high -> thread_surface_condition_degraded ->
    thread_contamination -> clamp_load_deficit
C2  tool_age_cycles_high + days_since_calibration_high ->
    torque_delivery_error -> over_torque OR under_torque
C3  material_lot_hardness_out_of_range -> cross_threading -> stud_damage
C4  fixture_wear -> hub_seating_gap -> uneven_clamp_distribution
C5  shift_C_night -> missed_manual_check -> defect_escapes_to_next_station
    (deliberate bias hook: night shift MUST be over-represented among escaped
     defects, so a fairness audit has something real to find)""",
    holdout="valve_stem_damage",
    consistency_rule=(
        "For thread_contamination the final torque stays INSIDE 110-125 Nm while the\n"
        "   knee angle is LATE (above 18 deg against a ~14 deg baseline) and the elastic\n"
        "   slope is LOW (below 2.6 against a ~3.2 Nm/deg baseline). That exact\n"
        "   combination -- in spec, wrong shape -- is the entire reason this system\n"
        "   exists. For under_torque the final torque is BELOW 110 Nm and the shape is\n"
        "   normal. For over_torque the curve flattens past yield."
    ),
    incident_focus=(
        "At least 20 must instantiate C1. At least 8 must instantiate C5 with\n"
        "   shift \"C\"."
    ),
    doc_prefix="WA",
)

BEARING = ComponentSpec(
    component_id="motor_bearing",
    description=(
        "Electric motor drive-end rolling element bearing, 6208-2RS,\n"
        "                shaft speed 1480 rpm, continuous duty"
    ),
    station="MB-03",
    inspection=(
        "vibration acceleration timeseries (spectral analysis) and\n"
        "                bearing housing temperature trend; visual check of seal condition\n"
        "                and lubricant colour"
    ),
    # electrical_fluting is the holdout and is deliberately ABSENT here. Listing
    # a class as detectable and then forbidding all mention of it gives the
    # generating model contradictory instructions, and the corpus that comes
    # back would quietly invalidate the unseen-defect measurement.
    defect_classes=(
        "bearing_wear, lubrication_degraded, misalignment,\n"
        "                contamination_ingress, looseness, cage_damage"
    ),
    standards=(
        "ISO 20816 (mechanical vibration - evaluation of machine\n"
        "                vibration), ISO 281 (rolling bearings - dynamic load ratings and\n"
        "                rating life), ISO 15243 (rolling bearings - damage and failures)"
    ),
    causal_graph="""B1  lubrication_interval_exceeded -> lubricant_film_breakdown ->
    friction_increase -> housing_temperature_rise + broadband_vibration_rise
B2  operating_hours_high -> raceway_fatigue -> bearing_wear ->
    outer_race_fault_frequency_harmonics
B3  coupling_misalignment -> radial_load_asymmetry -> misalignment ->
    twice_running_frequency_energy
B4  seal_degradation -> contamination_ingress -> abrasive_wear ->
    kurtosis_rise_before_rms_rise
B5  shift_C_night -> missed_lubrication_round -> lubrication_degraded
    (deliberate bias hook: night shift MUST be over-represented among missed
     preventive maintenance, so a fairness audit has something real to find)""",
    holdout="electrical_fluting",
    consistency_rule=(
        "Kurtosis rises BEFORE RMS on incipient faults -- an early-stage incident must\n"
        "   show elevated kurtosis with near-nominal RMS. Temperature and broadband\n"
        "   vibration rise together for lubrication faults. Misalignment shows energy at\n"
        "   twice running frequency (about 49.3 Hz at 1480 rpm), not at the bearing fault\n"
        "   frequencies. Do not report a fault frequency that contradicts the cause."
    ),
    incident_focus=(
        "At least 18 must instantiate B1. At least 8 must instantiate B5 with\n"
        "   shift \"C\". At least 10 must be incipient (kurtosis up, RMS still normal)."
    ),
    doc_prefix="MB",
)

PCB = ComponentSpec(
    component_id="pcb_assembly",
    description=(
        "Surface-mount PCB assembly, 0402 and 0603 passives plus QFN\n"
        "                packages, lead-free SAC305 reflow"
    ),
    station="PA-07",
    inspection=(
        "automated optical inspection of solder joints and component\n"
        "                placement; solder paste volume from the printer; reflow oven zone\n"
        "                temperature profile"
    ),
    # head_in_pillow is the holdout and is deliberately ABSENT here — see the
    # note on motor_bearing.
    defect_classes=(
        "insufficient_solder, cold_joint, tombstoning,\n"
        "                component_offset, bridging, missing_component, solder_void"
    ),
    standards=(
        "IPC-A-610 (acceptability of electronic assemblies),\n"
        "                IPC J-STD-001 (requirements for soldered electrical and electronic\n"
        "                assemblies), IPC-7095 (design and assembly process implementation\n"
        "                for BGAs)"
    ),
    causal_graph="""P1  stencil_aperture_clogged -> paste_volume_low ->
    insufficient_solder -> open_joint
P2  reflow_peak_temperature_low -> incomplete_wetting -> cold_joint
P3  placement_nozzle_wear -> component_offset -> tombstoning
P4  paste_volume_high + fine_pitch -> bridging -> short_circuit
P5  shift_C_night -> delayed_stencil_clean -> paste_volume_low
    (deliberate bias hook: night shift MUST be over-represented among missed
     stencil cleaning intervals, so a fairness audit has something real to find)""",
    holdout="head_in_pillow",
    consistency_rule=(
        "This component is VISION-DOMINANT: most defects are found optically, and\n"
        "   sensor evidence is thin. Paste volume is reported as a percentage of nominal\n"
        "   aperture volume; insufficient_solder incidents must show volume BELOW 70%.\n"
        "   Cold joints must show a reflow peak below 235 C for SAC305. Tombstoning must\n"
        "   involve a passive of 0402 or 0603 size, never a QFN."
    ),
    incident_focus=(
        "At least 18 must instantiate P1. At least 8 must instantiate P5 with\n"
        "   shift \"C\". Because this component is vision-dominant, at least 40 of the 60\n"
        "   must have been found by optical inspection rather than by a sensor reading."
    ),
    doc_prefix="PA",
)

COMPONENTS = (WHEEL, BEARING, PCB)


TEMPLATE = """# Corpus generation prompt — {component_id}

Paste everything inside the fence below into a capable model (GPT-5.x, Claude,
Gemini Pro). Save the reply as `data/corpus/raw/{component_id}.jsonl`, then run:

```bash
python -m data.generators.corpus_ingest data/corpus/raw/{component_id}.jsonl
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
component_id:   {component_id}
component:      {description}
station:        {station}
inspection:     {inspection}
defect_classes: {defect_classes}
standards:      {standards}

### CAUSAL GRAPH  (every incident MUST instantiate one of these chains)
{causal_graph}

### HOLDOUT — NEVER MENTION
Do not reference {holdout} anywhere, in any record, in any wording. It is
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
   {incident_focus}

SCHEMA — knowledge
{{"type":"knowledge",
 "doc_id":"SOP-{doc_prefix}-114",
 "component_id":"{component_id}",
 "doc_type":"sop",
 "title":"Short descriptive title",
 "defect_classes":["one_or_more_from_the_list_above"],
 "station":"{station}",
 "effective_date":"2025-03-01",
 "version":"3.1",
 "standard_ref":null,
 "sections":[
   {{"chunk_id":"SOP-{doc_prefix}-114#4.2",
     "heading":"4.2 Short section heading",
     "text":"180-400 words of specific, procedural prose."}}
 ]}}

SCHEMA — incident
{{"type":"incident",
 "doc_id":"INC-2025-0412",
 "component_id":"{component_id}",
 "occurred_at":"2025-04-12T06:20:00Z",
 "station":"{station}",
 "tool_id":"TOOL-ID-04",
 "shift":"A" | "B" | "C",
 "unit_ref":"UNIT-04412",
 "observed":"What the operator and instruments actually saw, with concrete
             measurements and units.",
 "measurements":{{"named_measurement":0.0}},
 "initial_hypothesis":"What was suspected first. OFTEN WRONG.",
 "confirmed_cause":"one_defect_class_from_the_list",
 "causal_chain":["node","node","node"],
 "resolution":"What was actually done about it.",
 "outcome":"What happened afterwards, including whether it recurred.",
 "time_to_resolve_hours":6.5,
 "units_affected":14}}

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
   {consistency_rule}

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

9. NEVER mention {holdout}.
````
"""


def build() -> list[Path]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for spec in COMPONENTS:
        path = OUT_DIR / f"{spec.component_id}.md"
        path.write_text(
            TEMPLATE.format(
                component_id=spec.component_id,
                description=spec.description,
                station=spec.station,
                inspection=spec.inspection,
                defect_classes=spec.defect_classes,
                standards=spec.standards,
                causal_graph=spec.causal_graph,
                holdout=spec.holdout,
                consistency_rule=spec.consistency_rule,
                incident_focus=spec.incident_focus,
                doc_prefix=spec.doc_prefix,
            ),
            encoding="utf-8",
        )
        written.append(path)
    return written


if __name__ == "__main__":
    for path in build():
        print(f"wrote {path}")
