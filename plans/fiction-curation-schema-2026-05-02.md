# Fiction-curation schema design — 2026-05-02

## The load-bearing principle

**A faithful fiction-AST is bigger than the prose it encodes.** Prose is a dense
serialization of structured information that human readers decompress on the
fly. A structured representation that captures only "plot points" is a glorified
summary, not a substrate for reasoning over the work. The schema must be
capacious enough that, if extraction were perfect, every claim a thoughtful
reader could make about the novel could be derived from the AST without
re-reading the prose.

A 100K-word novel will produce on the order of millions of atoms across the
schema layers below. That's the right shape. Anything substantially smaller is
lossy.

This is also why the schema is not ONE schema but a set of composable schemas
— per the schema-as-atom recursion, each layer is its own schema atom that
references the others. Curators (human or LLM) extract under one or more
layers; partial extractions are valid (some readers care about events, some
about epistemic state, some about thematic structure).

## The layered atom-type catalog (fiction-specific)

Twelve schema atoms. Each is a propstore family. Each can be extracted
independently and references atoms from other layers via stable IDs.

### Layer 1 — Surface

The literal text of the source, addressable by chapter / paragraph / sentence.
This is the substrate for all other layers' provenance pointers.

- **Atom type**: `surface_segment`
- **Captures**: chapter number + name, paragraph index, sentence index, raw
  text, character offset. Hash of the canonical text is part of the ID so
  edition differences are visible.
- **Literature**: trivial — narratological "text segmentation." Bal § 1
  (story / fabula / text distinction).
- **Propstore mapping**: a new family, document type
  `SurfaceSegmentDocument`. Placement: `HashScattered` keyed by
  `(source_atom_hash, chapter, paragraph)`.
- **New work**: family definition, no schema extensions to propstore needed.

### Layer 2 — Entity

The persistent things in the world: characters, locations, organizations,
objects, concepts, species, languages, technologies. With identity tracking
across mentions including coreference resolution.

- **Atom types**: `entity_definition`, `entity_mention`,
  `entity_alias`, `entity_property`
- **Captures**: canonical entity ID; first appearance (surface_segment ref);
  coreferent mentions; aliases (Mara Jade Skywalker, the Emperor's Hand,
  Karrde's smuggling associate); entity properties asserted in-text
  (Force-sensitive, red hair, owned weapons, affiliations).
- **Literature**: standard NER + coref (BERT-era). For long-form: Indexter
  (Cardona-Rivera et al.) on narrative-time-indexed entity reference;
  metanovel `papers/` has Cardona-Rivera plus character-modeling work.
- **Propstore mapping**: extends the existing concept/concept_alias
  machinery (`families/concepts/`, already cross-cited across active
  Mara/Luke/Thrawn). Some entities are concepts in propstore's existing
  sense; others are sui generis fictional entities. The boundary is
  fuzzy and probably wants a `fictional_entity` flag on the existing
  concept schema rather than a parallel family.

### Layer 3 — Event

Discrete actions and occurrences with full argument structure: agent, patient,
instrument, location, time, manner, purpose. Events are the load-bearing atoms
for causal and epistemic reasoning.

- **Atom types**: `event_assertion`, `event_argument`,
  `event_temporal_position`
- **Captures**: event predicate (e.g., `learn`, `attack`, `travel_to`,
  `say`), arguments per role, surface_segment provenance, narrative-time
  position (chapter/scene/turn), real-world-time if asserted in-text,
  duration if applicable.
- **Literature**: GLUCOSE (Mostafazadeh) — semantically grounded everyday
  causal reasoning. ATOMIC (Sap) — inferential commonsense knowledge.
  FrameNet (Baker) — frame-semantic argument structure, in metanovel's
  `papers/`. PropBank-style argument labels also relevant.
- **Propstore mapping**: new family `event_atoms`. References Layer 2
  entities by canonical ID. References Layer 1 surface_segments for
  provenance. Halpern-Pearl SCMs in `world/actual_cause.py` already
  reason over event variables — the new family produces those variables
  in a structured shape.

### Layer 4 — Causal

Directed causal relationships among events and states. The narrative-review
proposed `causes`/`enables`/`prevents` edges; propstore implemented Halpern-
Pearl SCM machinery instead. This layer produces the SCM input atoms.

- **Atom types**: `causal_assertion`, `structural_equation`,
  `intervention_target_set`
- **Captures**: cause event(s), effect event/state, modal strength
  (deterministic / enabling / preventing / probabilistic with weight),
  surface_segment provenance, in-narrative warrant if any. For
  structural equations, the variable set and the equation form.
- **Literature**: Trabasso & van den Broek (causal-network model of story
  comprehension); Chambers-Jurafsky (narrative event chains); Halpern-
  Pearl (actual cause); Schank-Abelson scripts.
- **Propstore mapping**: feeds `world/actual_cause.py`,
  `world/intervention.py`, `world/model.py`. Existing
  `StructuralCausalModel` and `StructuralEquation` types already
  exposed from `propstore.__init__.py`. New family produces atoms in
  the right shape; no new world machinery needed.

### Layer 5 — Epistemic state

Per-character (or per-organization) belief and knowledge states, indexed by
narrative time. The pragmatic question this answers: "what does Luke believe
about Mara as of chapter 7."

- **Atom types**: `epistemic_state_assertion`,
  `belief_revision_event`, `knowledge_acquisition_event`
- **Captures**: holder (entity ID), proposition (claim ref), modal type
  (knowledge / belief / suspicion / hope / fear / unknown), narrative-time
  index, justification if any (what surface event made the holder come to
  hold this), surface_segment provenance.
- **Literature**: epistemic logic (Hintikka). Theory of mind in story
  comprehension (Zunshine). ATMS-style assumption tracking
  (de Kleer 1986). Belief revision (AGM-Levi-Hansson).
- **Propstore mapping**: extends propstore's existing ATMS infrastructure
  (`world/atms.py`, 2967 lines per inventory) which already does
  assumption-set tracking. ATMS was designed for scientific reasoning;
  applying it to per-character epistemic states inside a fiction is a
  reuse, not a new build. The narrative-time indexing is the
  worldline-step machinery (see Layer 8).

### Layer 6 — Continuity / identity

Tracks the persistence of entities across mentions, including transformations
(character growth, lightsaber inherited, planet renamed), changes of identity
(disguise, undercover, secret identity revealed), and the resolution of
ambiguous reference.

- **Atom types**: `continuity_assertion`, `identity_transformation`,
  `identity_revelation`
- **Captures**: source entity ID, target entity ID, transformation type
  (continuous-evolution / discrete-replacement / partial-identity /
  pseudonym-resolution), narrative-time of transformation, surface
  evidence.
- **Literature**: object permanence in narrative comprehension
  (Glenberg); identity in fiction (Margolin).
- **Propstore mapping**: new family. Critical for the canonicity case —
  Mara Jade Skywalker (Legends) and Mara Jade (Canon-pre-2014) and
  Mara Jade (Canon-post-2014, mostly absent) are arguably the same
  entity under different identity conditions. Continuity atoms make
  this explicit and arguable.

### Layer 7 — Spatial

The geography of the story: locations, containment relations, travel
routes, distances when asserted, in-narrative spatial relationships
("two parsecs from Coruscant").

- **Atom types**: `location_assertion`, `containment_relation`,
  `spatial_path`, `spatial_distance`
- **Captures**: location entity ID, containing-location ID, travel
  endpoints, asserted distance/duration, surface_segment provenance.
- **Literature**: spatial-cognition in narrative (Zwaan event-indexing
  model — also covers temporal/causal/protagonist/intentional axes).
- **Propstore mapping**: new family. Could compose with existing concept
  relationships if locations are modeled as concepts.

### Layer 8 — Temporal / structural

Narrative time vs story time. Order, duration, frequency. Time skips,
flashbacks, parallel scenes. Chapter/act/scene boundaries. POV/focalization
shifts.

- **Atom types**: `narrative_time_position`, `temporal_relation`,
  `pov_shift`, `focalization_assertion`, `scene_boundary`,
  `time_skip_event`
- **Captures**: narrative-time index, story-time anchor (if available),
  ordering relations between events, focalizer entity ID, scene/chapter
  membership.
- **Literature**: Genette (order, duration, frequency, mood, voice) is the
  canonical reference. Bal extends Genette. Indexter for narrative-time
  representation. Story grammars (Mandler-Johnson, Rumelhart, Thorndyke).
- **Propstore mapping**: this is where worldlines are the load-bearing
  primitive. Each chapter is a worldline step (or a worldline
  revision). Time-skips are gaps in the worldline-step sequence with
  explicit `time_skip_event` atoms. POV shifts are metadata on
  worldline steps. The narrative-review's `world.at(time)` query is now
  implemented through the worldline-journal bridge: `pks worldline
  build-journal` captures a durable `TransitionJournal`, and
  `pks worldline at-step NAME STEP` projects the accepted claim view for
  a chapter/scene step. See
  `plans/worldline-journal-bridge-2026-05-02.md`.

### Layer 9 — Pragmatic / dialog

For every spoken line: speaker, addressee, propositional content,
illocutionary force, presuppositions, implicatures, in-character lies and
sincerity assessments.

- **Atom types**: `dialog_turn`, `speech_act`,
  `presupposition_assertion`, `implicature_assertion`,
  `truthfulness_assessment`
- **Captures**: speaker entity ID, addressee(s), surface text reference,
  propositional content (claim ref), illocutionary force (assertion /
  request / promise / question / etc.), presuppositions of the utterance,
  conversational implicatures derivable from context, whether the speaker
  is sincere / lying / mistaken / ironic / quoting.
- **Literature**: Austin (How to Do Things With Words). Searle (speech
  acts). Grice (cooperative principle, implicature). Brown-Levinson
  (politeness theory). For literary dialog specifically, conversation-
  analytic work and reader-response on dialog interpretation.
- **Propstore mapping**: new family. Pragmatic atoms make Mara's deception
  about her identity to Karrde queryable as deception-with-known-truth-
  value rather than just dialog text.

### Layer 10 — Thematic / motif

Recurring images, symbols, callbacks, foreshadowing, leitmotifs, thematic
echoes. The hardest layer to extract, the most contested.

- **Atom types**: `motif_instance`, `thematic_assertion`,
  `foreshadowing_link`, `callback_link`, `intertextual_reference`
- **Captures**: motif/theme identifier, surface_segment instance,
  associations (this scene callbacks that earlier scene), foreshadowing
  pairs, intertextual targets (other works referenced).
- **Literature**: Booth (rhetoric of fiction); Frye (anatomy of criticism);
  Genette (palimpsests on intertextuality); Kristeva on intertext.
- **Propstore mapping**: new family. Naturally has higher uncertainty
  opinions than other layers — thematic readings are interpretive.
  Multiple curators will assert competing motif catalogs; the substrate
  preserves all of them with provenance and the reader's PAF projects
  under chosen interpretive framework.

### Layer 11 — Authorial / extra-textual

Author intent statements, paratextual material (prefaces, afterwords),
stylistic features, authorial commentary outside the work, editorial
revisions across editions.

- **Atom types**: `authorial_intent_assertion`,
  `paratext_assertion`, `style_feature_assertion`,
  `revision_event`
- **Captures**: author entity ID, source of the assertion (interview,
  preface, tweet, manuscript edit), date, propositional content,
  pertinence to which atoms in other layers.
- **Literature**: Genette (paratexts). Barthes "Death of the Author"
  surfaces a methodological choice — the substrate represents authorial
  intent atoms but readers can choose to weight them at zero if they
  prefer textualist projection.
- **Propstore mapping**: new family. Critical for the Star Wars case —
  Lucas's interviews about Force lore, Filoni's clarifications,
  Karen Traviss's blog posts about Mandalorian culture — all are
  authorial assertions whose weight depends on the reader's hermeneutic.

### Layer 12 — Provenance / curation meta

Per the cross-conversation atom catalog: every extracted atom is itself a
curation atom signed by its curator (LLM run + human validator + their
respective keys), targeting a surface_segment or set of segments.

- **Atom types**: `curation_assertion`, `extractor_provenance`,
  `validation_assertion`
- **Captures**: extractor identity (human key, or LLM-as-curator with
  model version + run ID + prompt hash), validator identity if
  human-validated, target surface_segments, target atom hashes,
  confidence opinion `(b, d, u, a)` per Jøsang.
- **Literature**: this is the propstore federation atom catalog applied
  to fiction.
- **Propstore mapping**: reuses the federation curation-atom design.
  Per-curator trust opinions compose with per-domain trust ("I trust
  GPT-5 for entity extraction with `(b=0.85, u=0.10)` but for thematic
  assertions only `(b=0.40, u=0.50)`"). Layer 12 makes the
  trust-calibration of LLM-driven extraction first-class.

## Atom volume sanity check

For a chapter of ~5000 words (Heir to the Empire chapter 1 is roughly that):

| Layer | Estimated atom count per chapter |
|---|---|
| Surface | ~250 (sentences) |
| Entity | ~50 mentions, ~15 unique entities, ~30 alias atoms, ~40 property atoms = ~135 |
| Event | ~100 events × avg 4 arguments = ~500 |
| Causal | ~30 causal links + ~10 SCM equations = ~40 |
| Epistemic | ~40 belief states across ~5 characters = ~200 |
| Continuity | ~10 |
| Spatial | ~30 |
| Temporal/structural | ~40 |
| Pragmatic | ~30 dialog turns × ~3 atoms each = ~90 |
| Thematic | ~30 (high variance) |
| Authorial | ~5 |
| Curation meta | one per atom above ≈ ~1300 |

**Total per chapter: ~2700 content atoms + ~1300 curation atoms ≈ 4000 atoms.**
Heir to the Empire has ~30 chapters. **Full novel: ~120,000 atoms.**

A 5000-word chapter producing 4000 structured atoms is roughly 1:1.25 by token
count if each atom averages ~50 tokens of structured representation. Across
content + curation meta, the AST exceeds the prose by maybe 5-10× by
token weight. **Yes, the AST is meaningfully bigger than the novel.** That's
the cost of capacity.

## Worked example: HttE Chapter 1, opening scene

The opening of Heir to the Empire shows Captain Pellaeon on the bridge of the
*Chimaera*, with Grand Admiral Thrawn issuing orders. Decomposition:

- **Surface**: ~30 paragraphs, ~150 sentences as `surface_segment` atoms.
- **Entity**: Pellaeon (introduce, Imperial captain, of the Chimaera, human),
  Thrawn (introduce, Grand Admiral, blue-skinned, red-eyed, of the
  Chimaera), Chimaera (introduce, Imperial Star Destroyer), Empire
  (existing concept), Imperial Navy (existing). Aliases: "the Grand Admiral",
  "the captain", "the Chiss" (revealed later, foreshadowed?). Properties:
  Thrawn.species=Chiss (asserted via "blue-skinned, red-eyed" plus
  Layer-11 authorial atom citing later disclosure), Pellaeon.rank=Captain.
- **Event**: Pellaeon-stands-at-bridge, Thrawn-issues-order, etc. Argument
  structure for each.
- **Causal**: Thrawn's strategic instructions cause subsequent Imperial
  movements (causal links forward to events in later chapters).
- **Epistemic**: Pellaeon-believes-Thrawn-is-strategic-genius (asserted via
  internal-monologue surface text); Pellaeon-knows-Thrawn-is-Chiss (yes
  by chapter 1) but the *reader* doesn't know fully what that means.
- **Spatial**: Chimaera contains-bridge; Chimaera in-Imperial-controlled-
  region.
- **Temporal**: chapter 1 marks beginning of narrative time; story-time is
  ~5 ABY anchored by Layer-11 author/paratext atoms; this scene is scene
  1 of chapter 1.
- **Pragmatic**: Thrawn's orders are commands (illocutionary force), with
  presuppositions about Pellaeon's competence; Pellaeon's responses are
  acknowledgments.
- **Thematic**: introduces motif of "Imperial-rebirth-under-superior-
  intellect"; foreshadows Thrawn-is-different-from-prior-Imperial-leaders.
- **Authorial**: Zahn introduces a non-human Imperial as protagonist —
  paratextually significant in the larger Star Wars context.
- **Curation meta**: every atom above is an extractor-signed curation atom
  with confidence opinion.

That's one scene. The substrate captures it all without privileging any one
layer. A reader interested only in plot reads through the event layer; a
reader interested in narrative theory reads through Genette-shaped atoms
in the temporal/structural layer; a reader interested in canon disputes
reads through epistemic + continuity + authorial layers.

## Extraction strategy

LLM-assisted with human validation, per layer:

1. **LLM extraction passes** — one pass per layer, prompted with the schema
   atom for that layer, structured output. Tracking via curation atoms
   signed by `(model_version, prompt_hash, run_id)`.
2. **Human validation samples** — per layer, sample N atoms, validate or
   reject. Validation results update the per-(LLM, layer) trust opinion
   for use in future extractions.
3. **Cross-layer consistency check** — entity references match across
   layers, event arguments resolve to known entities, etc.
4. **Calibrated trust** — each curator (LLM or human) builds up a
   per-(layer, domain) reliability opinion that weights downstream uses
   of their extracted atoms.

## What this requires from propstore

In rough order of necessity:

1. **No new propstore code for layers 1, 7, 9, 10, 11** — they're new
   families (FamilyDefinition entries) using existing schema, placement,
   and contract machinery.
2. **Layer 2 (entity)** — extends the existing concept family with a
   `fictional_entity` flag; possibly a new alias-handling path.
3. **Layer 3 (event)** — new family for event atoms with frame-style
   argument structure.
4. **Layer 4 (causal)** — produces atoms in the shape `world/actual_cause.py`
   and `StructuralCausalModel` consume; minimal propstore changes if any.
5. **Layer 5 (epistemic)** — reuses ATMS infrastructure; maps each
   character's belief state to an ATMS context. Possibly minor
   adapter work.
6. **Layer 6 (continuity)** — new family.
7. **Layer 8 (temporal/structural)** — load-bearing on worldlines.
   The worldline-journal bridge is available for claim-membership views
   at a chapter/scene step. Inter-step stance/conflict reconstruction is
   still conditional heavy-bridge work, not part of the minimal Layer 8
   precondition.
8. **Layer 12 (curation meta)** — IS the federation curation-atom
   pattern from the broader proposal. Needs the per-curator trust
   opinion machinery, which is mostly there in `propstore/opinion.py`
   plus the `source_trust_argumentation/` WS-K mapping.

So: ~7 new families, 0-2 propstore extensions depending on what the
worldline scout finds, no new world/argumentation kernel work, full
reuse of opinion algebra and ATMS for trust + epistemic state.

## Open design decisions

1. **Entity vs concept boundary.** Are "Mara Jade" and "Coruscant"
   propstore concepts (with the existing concept machinery) or sui generis
   fictional entities? Recommend: concepts with `fictional_entity=true`
   flag — reuses existing alias / parameterization / search infrastructure.

2. **Event argument vocabulary.** PropBank, FrameNet, AMR, or a fiction-
   specific frame inventory? Recommend: FrameNet-derived because metanovel's
   `papers/` already has Baker's FrameNet paper and frame inventories are
   well-documented for English fiction.

3. **Granularity of epistemic state.** Per-character per-scene? Per-character
   per-event? Recommend: per-event for atoms that are actually asserted in
   text; readers project intermediate states by interpolation.

4. **Thematic layer authority.** Who's allowed to assert motif atoms?
   Recommend: anyone, with thematic-curator trust opinions. Different
   readers will subscribe to different thematic-curator labelers (a
   feminist-critical reading vs. a Joseph-Campbell reading vs. a
   close-formal reading).

5. **Authorial intent weighting default.** Should the default trust
   weighting honor authorial statements highly, or zero them per Barthes?
   Recommend: default to medium-weight, configurable per-reader. This is
   exactly the Jøsang opinion mechanism doing its job.

6. **Edition / version handling.** HttE has multiple editions with minor
   prose differences. Should each edition be a separate source atom?
   Recommend: yes, with lifting rules between editions for non-substantive
   variants. Substantive revisions (rare in HttE but common elsewhere)
   produce different curation atoms.

7. **Translation handling.** HttE in German has different prose. Is the
   German-translation an edition (lift via "translation-equivalent" rule)
   or a different source atom for the German-canon? Recommend: separate
   source atom; lifting rules where translation is faithful; non-lifted
   for translation-loss artifacts.

## Pre-implementation gates

Before authoring schema atoms in propstore:

1. **Use the completed worldline-journal bridge for Layer 8** — author
   chapter/scene tracks as worldline journals and query with
   `pks worldline at-step`. Escalate to the heavy bridge only if the demo
   needs historical stances/conflicts, not just accepted claim membership.
2. **Pick the event-argument vocabulary** — recommend FrameNet, defer
   final pick to confirm metanovel's FrameNet paper is current enough.
3. **Decide on entity-vs-concept boundary** — propose `fictional_entity`
   flag, await Q's view.
4. **Establish baseline curator trust opinions** — initial calibration
   against a hand-curated test set so LLM-extraction outputs land with
   non-vacuous opinions.

## Estimated work to first demo

Assuming the completed worldline-journal bridge is sufficient for the first
demo's temporal claim-membership queries:

- Schema atoms (12 families, family registry entries, document type
  definitions, contract manifests): ~1 week
- Extractor harness (LLM-driven extraction per layer, prompts, output
  parsing, curation-atom emission): ~1 week
- HttE Chapter 1 extraction + human validation: ~3-4 days
- Cross-layer consistency tooling: ~3-4 days
- CLI surface for querying: ~3 days
- Demo presentation (the OverlayWorld-as-canonicity-track Mara-Jade
  query, with three reader policies): ~2-3 days

**Total to first single-chapter demo: ~3 weeks of focused work.**

For full HttE: ~8-12 weeks LLM-extraction + spot-validation + curation
trust calibration. The demo is shippable after one chapter; full novel
becomes the "stress at scale" follow-up.

## Why this design works

Three structural properties:

1. **Each layer is independently extractable.** A reader can use only the
   event layer and have a workable plot summary; another can use the
   pragmatic + thematic layers for a literary-critical reading. The
   substrate doesn't impose a totality-or-nothing tax.

2. **Every atom carries provenance and per-curator trust.** No claim is
   asserted without attribution. Disputes about entity coreference,
   thematic interpretation, or causal structure compose through the
   existing argumentation + Jøsang machinery.

3. **The substrate is domain-general.** Nothing in this schema is
   Star-Wars-specific or even fiction-specific in a deep way. The same
   12 layers apply to historical narrative, biography, journalism,
   long-form interview transcripts. The Mara-Jade demo is a stress
   test; the schema is reusable.

If extraction works at chapter scale, the substrate does what it claims
to do: capture everything, structure the disagreements, project per
reader. **That's the test.**
