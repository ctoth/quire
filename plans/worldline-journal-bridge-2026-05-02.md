# WorldQuery ↔ EpistemicSnapshot bridge — design 2026-05-02

## 1. Motivation

Propstore today has two parallel surfaces for "what claims hold":

- **`WorldQuery`** (`world/model.py:228-265`) projects claim views from a frozen
  sidecar build state. No temporal axis.
- **`EpistemicSnapshot` / `TransitionJournal`** (`support_revision/history.py:77-364`)
  represents AGM revision states with full belief-base contents and typed
  semantic diffs (`diff_epistemic_snapshots`, `history.py:408-424`).

A reader cannot today ask "what claims hold *as of journal step k*." This blocks
two downstream pieces of work:

1. **Federation** (per `plans/distributed-layer-proposal-2026-05-02.md`) — cross-
   repo projection over `(local atoms ∪ fetched-foreign-via-trusted-bridges)`
   needs reader-side temporal projection that doesn't exist in the substrate.
2. **Fiction-curation Layer 8** (per `plans/fiction-curation-schema-2026-05-02.md`)
   — "what does Mara know as of chapter 7" is the load-bearing query for the
   Mara-Jade demo and depends on a journal-step API that doesn't exist.

This document specifies the bridge. It is small (~100 lines of code) and
paper-backed.

## 2. Existing primitives

Verified by direct code read (`reports/distrib/bridge-code-scout.md`):

### Atom-id schemes are disjoint, joined only via `source_claims`

`WorldQuery` addresses claims by sidecar `claim_core.id` (`model.py:128-147`).
`EpistemicSnapshot` addresses atoms by `AssertionAtom.atom_id`, which equals
`str(SituatedAssertion.assertion_id)` (`state.py:50-53`) — content-derived from
relation/role-bindings/context/condition/provenance-ref. The `ps:assertion:`
prefix is the assertion-id namespace (`history.py:472-477`,
`revision_capture.py:91, 105`).

The link between schemes is preserved one-way: every `AssertionAtom` carries
`source_claims: tuple[ActiveClaim, ...]` (`state.py:43-58`,
`projection.py:69-75`). Each `ActiveClaim` carries its `claim_id`. So the
reverse map (atom → claim_ids) is recoverable from the snapshot itself.

### `project_belief_base` is the existing one-way bridge

`project_belief_base(bound: BoundWorld, *, include_assumptions=True) -> BeliefBase`
(`projection.py:35-99`) projects a bound-world's active claims into the AGM
representation. Lossy: drops non-EXACT support, collapses values to JSON-string
role bindings, reduces conditions and provenance to digests, doesn't represent
stances.

This bridge runs `WorldQuery → BeliefBase`. We need the reverse direction
(`EpistemicSnapshot → claim view`) for `at_journal_step`.

### `TransitionJournal` exists but is unused inside propstore

`TransitionJournal(entries=())` (`history.py:304-364`) carries a sequence of
`TransitionJournalEntry`, each with `state_in`, `operation`
(`TransitionOperation`, `history.py:134-169`), `policy_id`, operator (REVISE /
CONTRACT / ITERATED_REVISE), `state_out`, and a content-addressable hash.
`TransitionJournal.replay()` re-executes via `support_revision.dispatch.dispatch`.

**Zero in-tree constructors.** `WorldlineDefinitionDocument`
(`families/documents/worldlines.py:124-153`) holds ONE optional revision, ONE
optional result, ONE optional revision-state. Single-episode, not journal-shaped.
This gap is part of the bridge work.

### `belief_set` package surface

`C:/Users/Q/code/belief-set/` provides AGM (`agm.py:103-227`, Darwiche-Pearl
[1997] bullet revision over Spohn ranks per `Spohn` [1988]), iterated revision
(`iterated.py:17-100`, lexicographic per Nayak-Spohn and restrained per
Booth-Meyer [2006]), IC merge (`ic_merge.py:43-79`, Konieczny-Pino-Pérez [2002]
SIGMA/GMAX). Atom domain is `frozenset[str]` worlds (`language.py:7`). No
journal/step API; no mapping to `ps:assertion:*`.

belief_set is not directly composable with the bridge — different atom domain.
Out of scope here; revisit if cross-ref IC merge of snapshots becomes needed.

## 3. Theoretical grounding

### Bonanno: branching-time AGM with PLS

The journal *is* a branching-time history in the sense of Bonanno [2007]
(`propstore/papers/Bonanno_2007_AGMBeliefRevisionTemporalLogic/notes.md`) and
Bonanno [2010, 2012]
(`propstore/papers/Bonanno_2010_BeliefChangeBranchingTime/notes.md`). Bonanno
axiomatizes AGM postulates K*1-K*8 inside a branching-time modal logic with
frames `⟨T, →, Ω, B_t, I_t⟩` — time-indexed belief and information operators
over a branching tree of histories. The Qualitative Bayes Rule is the semantic
core relating belief at successor states to information received.

Bonanno [2010] proves AGM-consistency equivalent to a directly-checkable frame
property (PLS — the *Past Looks Same* condition) and generalizes iterated
revision as a ternary `B(h, K, φ)` over histories. **PLS is the structural
invariant the bridge must preserve when composing snapshots through a
`TransitionJournal`.** Stated informally: at any branching point, if two
histories had identical belief states up to the branch, they continue to be
identical-up-to-relabeling under the same information at the branch.

Operationally for us: `apply_epistemic_diff(state_in, diff)` (`history.py:427-455`)
already validates that the resulting hash matches the expected target. This is
PLS-shaped at the implementation level; Bonanno gives the formal name and the
correctness statement we can cite.

### Dixon: ATMS-into-AGM behavioural equivalence

Dixon [1993]
(`propstore/papers/Dixon_1993_ATMSandAGM/notes.md`) proves Theorem 1: for any
ATMS justification graph plus a context selection function, the resulting
in/out classification is *behaviourally equivalent* to AGM revision over the
contextually-derived theory under epistemic entrenchment. The methodological
shape is exactly what we need: take a structurally-richer representation (ATMS
labels, here `EpistemicStateSnapshot.base.atoms` with full
`AssertionAtom.source_claims`) and project it into the leaner operational
interface (`WorldQuery.claims_by_ids`) with a behavioural-equivalence
correctness statement. **The bridge correctness theorem we need is Dixon-shaped:
projecting `state_out` of step `k` through `at_journal_step` agrees with the
`WorldQuery` view that would obtain if propstore had executed the journal's
operations directly.**

### Halpern-Pearl actual cause inherited

Propstore's `world/actual_cause.py` already implements actual-cause analysis
per the Halpern-Pearl trio
(`propstore/papers/Halpern_2000_CausesExplanationsStructural-ModelApproach`,
`Halpern_2005_CausesExplanationsStructuralModel`,
`Halpern_2015_ModificationHalpern-PearlDefinitionCausality`) and Pearl [2000]
(`Pearl_2000_CausalityModelsReasoningInference`). The bridge inherits this as
the semantics of any "why does this claim hold at step k" query — once the
bridge produces a projected claim view, existing Halpern-Pearl machinery
applies unchanged.

### AGM-on-AFs and the contraction asymmetry

Baumann [2015]
(`argumentation/papers/Baumann_2015_AGMMeetsAbstractArgumentation/notes.md`)
gives AGM expansion + revision for Dung AFs via Dung logics where ordinary
equivalence equals strong equivalence. Baumann [2019]
(`propstore/papers/Baumann_2019_AGMContractionDung/notes.md`) proves the Harper
Identity *fails* for Dung AFs; full AGM contraction requires dropping recovery.
**Implication for the bridge: contraction and revision are asymmetric. The
bridge must not treat a contraction step as just "revision by negation."** The
operator field on `TransitionJournalEntry` (`history.py:22-25`,
REVISE/CONTRACT/ITERATED_REVISE) already encodes this distinction; the bridge
must preserve it.

### External citations (not ingested locally)

Standard background; cited inline in the design where relevant:

- Alchourrón, Gärdenfors, Makinson [1985] "On the logic of theory change",
  *JSL* 50(2) — origin of AGM postulates.
- Darwiche & Pearl [1997] "On the logic of iterated belief revision", *AI*
  89(1-2) — DP postulates that any iterated-revision policy must satisfy.
  Already implemented in belief_set per code scout.
- Spohn [1988] "Ordinal conditional functions: A dynamic theory of epistemic
  states", in *Causation in Decision*. Ranking-function semantics underlying
  belief_set's `SpohnEpistemicState`.
- Reiter [1991] "The frame problem in the situation calculus", in *AI and
  Mathematical Theory of Computation*. Background — the bridge ducks the frame
  problem by using discrete snapshots rather than fluent extrapolation.
- Baltag, Moss, Solecki [1998] "The logic of public announcements, common
  knowledge, and private suspicions", *TARK VII*. Action-model product update.
  Each `TransitionJournalEntry.operation` corresponds, semantically, to an
  action-model update; PLS is the propstore-applicable form of action-model
  soundness.
- Konieczny & Pino-Pérez [2002] "Merging information under constraints",
  *JLC* 12(5). Foundation Coste-Marquis [2007] lifts to AFs; relevant when
  the bridge later needs to merge snapshots from multiple repos.
- Shapiro, Pagnucco, Lespérance, Levesque [2011] "Iterated belief change in
  the situation calculus", *AI* 175(1). The most directly-load-bearing prior
  bridge: revision operators integrated into a temporal action formalism with
  a soundness result. Our bridge is structurally analogous; we cite it as
  prior art for the composition we're doing.

## 4. Design — minimal bridge

Three additions, ~100 lines of code total.

### 4.1 `snapshot_to_claim_ids` helper

```python
# propstore/support_revision/projection.py
def snapshot_to_claim_ids(snap: EpistemicSnapshot) -> set[str]:
    """Recover the set of sidecar claim_ids that contributed to the
    accepted atoms in this snapshot.

    Uses the round-trip key already on every AssertionAtom
    (state.py:43-58, projection.py:69-75). Many-to-one is possible:
    one atom can have multiple source_claims (projection.py:69-75).
    """
    state = snap.state
    accepted = set(state.accepted_atom_ids)
    return {
        str(claim.claim_id)
        for atom in state.base.atoms
        if isinstance(atom, AssertionAtom)
        and atom.atom_id in accepted
        for claim in atom.source_claims
    }
```

Five lines. Pure projection. No new dependencies. Tests at unit level: an
empty snapshot returns the empty set; a single-claim snapshot returns
`{claim.claim_id}`; many-to-one collapse round-trips.

### 4.2 `WorldQuery.at_journal_step` method

```python
# propstore/world/model.py — add to WorldQuery
def at_journal_step(
    self,
    journal: TransitionJournal,
    k: int,
    *,
    rebind: bool = False,
) -> ClaimView:
    """Project the claims accepted at step k of the journal.

    Per Bonanno [2007, 2010]: the journal is a branching-time history;
    state_out at step k is the belief state along that history. Per
    Dixon [1993]: this projection is behaviourally equivalent to
    running the journal's operations against the live store, modulo
    the lossy projection at the AGM boundary (projection.py:46-47,
    164-185).
    """
    if not 0 <= k < len(journal.entries):
        raise IndexError(f"step {k} out of range for {len(journal.entries)}-step journal")
    snap = journal.entries[k].state_out
    claim_ids = snapshot_to_claim_ids(snap)
    rows = self.claims_by_ids(claim_ids)  # model.py:824-836
    if rebind:
        scope = snap.state.scope
        env = Environment(bindings=scope.bindings, context_id=scope.context_id)
        return self.bind(environment=env).restrict_to(claim_ids)
    return ClaimView(claims=rows, scope=snap.state.scope)
```

`ClaimView` is a thin new return type carrying `(claims: dict[str, ClaimRow],
scope: RevisionScope)` — the scope informs callers what bindings/context the
view is taken under.

`rebind=True` reconstructs a `BoundWorld` against the snapshot's
`RevisionScope` (`state.py:90-104`). The default `rebind=False` returns a
flat claim view — sufficient for federation reads and fiction-curation Layer
8 queries that don't need re-derivation.

Heavy variant (re-derive stances and conflicts) is deferred. See section 7.

### 4.3 Imports

`world/model.py` does not currently depend on `support_revision`
(`model.py:1-58`). The bridge introduces the dependency. The cycle risk is
real (`support_revision/projection.py` imports `BoundWorld` which is
`world.bound`); resolved by putting `snapshot_to_claim_ids` in
`support_revision/projection.py` (already imports `support_revision.state`)
and the `at_journal_step` method on `WorldQuery` importing the helper lazily
inside the method body. Standard pattern in propstore.

## 5. Design — TransitionJournal usage gap

`TransitionJournal` is defined but no in-tree code constructs one. The bridge
is useless without journals. Two work items:

### 5.1 Worldline-run journal capture

`revision_capture.capture_revision_state(bound, revision_query)`
(`revision_capture.py:12-56`) currently dispatches one operator and returns
one `WorldlineRevisionState`. Add a parallel `capture_journal(bound,
operations: Sequence[TransitionOperation]) -> TransitionJournal` that:

- Dispatches each operation in order via `support_revision.dispatch.dispatch`
- Captures `(state_in, operation, state_out, policy_payload)` per step into
  a `TransitionJournalEntry`
- Returns the assembled `TransitionJournal`

This is mechanical given the existing `dispatch` infrastructure. ~40 lines.

### 5.2 Journal storage

`WorldlineDefinitionDocument` (`worldlines.py:145-153`) currently holds one
revision. Extend with an optional `journal: TransitionJournal | None`
field. Backward compat: existing single-revision documents are re-readable
unchanged; new code writing journals also writes the journal field.

For the fiction-curation case, the natural construction is one journal per
canon-track (Heir-to-the-Empire-as-a-journal of N=30 chapter-revision
operations). For the federation case, journals may be exchanged across
repos (each entry is content-addressable per `history.py:242-244`).

## 6. WorldlineDefinition refactor

Conceptual cleanup that the bridge enables:

**Before**: a `WorldlineDefinition` is a single revision query over a fixed
belief space. "Worldline" is misleading — it names a single point.

**After**: a `WorldlineDefinition` is a sequence of revisions over a belief
space, captured as a `TransitionJournal`. The single-revision case
degenerates to a 1-step journal. "Worldline" now actually names a trajectory.

This aligns with the propstore-narrative-review.md (2026-03-22) intent:
worldlines should be temporal trajectories, not single-state belief spaces.
The review proposed a different mechanism (`valid_from`/`valid_until` on
claims); this design achieves the same observable semantics through journals
without modifying the claim schema.

Migration: schema-version bumps. New code reading old documents treats
single-revision as 1-step journal. Old code reading new documents reads the
revision field unchanged and ignores the journal field. No data migration
required.

## 7. Heavy variant — re-derived stances

The minimal bridge gives a claim-membership view at step k. It does NOT
re-derive:

- **Stances** (`relation_edge`) between claims that hold at step k. The
  snapshot doesn't carry them.
- **Conflicts** at step k. Conflict-detection runs against the full sidecar.
- **Derived values** at step k that depend on inference over the active
  graph at that state.

For these, the heavy variant: check out `state.scope.commit`
(`state.py:95`), rebuild the sidecar at that commit, then filter by the
snapshot's claim_id set. Cost: per-query sidecar rebuild. Likely too
expensive for live querying; cacheable per `(commit, claim_id_set)`.

**For fiction-curation Layer 8, the heavy variant is probably required.**
Stances between scenes ("Mara's chapter-7-belief that X *undercuts*
Luke's chapter-5-assumption that Y") are inter-state argumentation atoms,
not pure claim-membership facts. Likely Phase 2 of bridge work.

For first-cut federation reads (cross-repo projection of foreign atoms),
the minimal bridge suffices.

## 8. Correctness — properties, not proofs

**Truth in this design = what hypothesis cannot falsify.** Not what the
citations claim. Not what the implementer believes. Not what this doc
asserts. The property suite (section 11) is the bridge's contract; what
holds across N=1000 hypothesis cases is what we know.

Three principles, non-negotiable:

1. **Tests come first.** Every phase's deliverable list orders tests
   *before* the code that satisfies them. An empty Phase 1 deliverable
   is "the property suite is written and failing"; the implementation's
   job is to make it green. Per-phase acceptance criteria reference
   property identifiers (P1, P5, etc.) from section 11.

2. **Properties never weaken.** Once a property holds against N=1000
   hypothesis cases, it is the bridge's public contract. Future changes
   that would break it require either (a) explicit retirement with
   rationale committed alongside the change, or (b) a major version bump
   announcing the contract change. Default behavior: properties are
   forever. New features add properties; they do not remove or relax
   existing ones.

3. **The cited theorems are properties.** "Dixon-shape behavioural
   equivalence" is concretely property **P5**:
   `direct_dispatch(s, j.entries[:k+1]).all_claim_ids() ==
   s.at_journal_step(j, k).claim_ids()` over hypothesis-generated
   journals. The Bonanno PLS invariant is concretely property **P-PLS**:
   a diff/apply round-trip over applicable acceptance deltas generated from
   a source snapshot and then applied to that source. This is the PLS surface
   propstore's current epistemic-state representation can support because
   acceptance order is operational metadata, not a semantic belief-set member.
   If P5 falsifies, Dixon-shape equivalence didn't apply; if P-PLS falsifies,
   PLS didn't apply. Either way the *design* changes, never the property.

This is TDD as truth, not just process.

## 9. See also (adjacent, not load-bearing)

Per the relevance check
(`reports/distrib/cayrol-brewka-oikarinen-check.md`):

- **Cayrol [2014]** "Change in AFs" — already wired in
  `argumentation/src/argumentation/af_revision.py::_classify_extension_change`.
  The classifier categorizes single-argument-addition deltas as
  decisive/restrictive/questioning/destructive. Useful as a forward-pointer:
  each journal step that classifies as one of these can be tagged for UI
  surfacing. Not a bridge contract.
- **Oikarinen [2010]** "Strong Equivalence for AFs" — kernel-based
  equivalence (a-kernel/c-kernel/etc). NOT the correctness template
  (compositional substitutability ≠ behavioural equivalence). Useful as a
  *cache dedup oracle*: two journal steps producing different but
  kernel-equal AFs project to the same view; cache by kernel rather than by
  full AF. Future optimization, not Phase 1.
- **Brewka & Woltran [2010, 2013]** ADFs — out of scope. Different axis
  entirely (state-of-node-under-3-valued-labelling for one fixed framework).
  Tracked as future workstream WS-O-arg-aba-adf.

## 10. Executable implementation phases

Each phase has goal, files-to-touch, and acceptance criteria. Phase 0 is the
scope-check gate; Phase 6 is the docs-reconciliation finalization. Phases 1
and 2 are independent and can run in parallel; phases 3 and 4 are conditional.

### Phase 0 — scope check (~2 hours)

**Goal:** confirm this design's assumptions still hold against current
propstore HEAD before any code is written.

**Files:** scope-check report (`reports/distrib/bridge-phase0-scope-check.md`),
no code changes.

**Acceptance criteria:**
- Re-verify these load-bearing assertions against HEAD:
  - `world/model.py:824` `claims_by_ids` signature unchanged.
  - `support_revision/state.py:43-58` `AssertionAtom.source_claims` unchanged.
  - `support_revision/history.py:172-270, 304-364` `TransitionJournal*` types
    unchanged.
  - `families/documents/worldlines.py:124-153` `WorldlineDefinitionDocument`
    still single-revision.
  - `belief_set` API at `C:/Users/Q/code/belief-set/` (verify `agm.py`,
    `iterated.py`, `ic_merge.py` exports unchanged).
- Confirm `propstore/papers/Bonanno_2007_*`, `Bonanno_2010_*`, `Dixon_1993_*`
  paths still resolve.
- Output: zero-delta confirmation, OR a delta report with adapted plan
  (which becomes a re-design gate, not direct execution).

### Phase 1 — minimal bridge (~3-4 days)

**Goal:** ship `WorldQuery.at_journal_step` for the read direction (federation
case) plus the correctness property test.

**Files to touch:**
- `propstore/support_revision/projection.py` — add `snapshot_to_claim_ids`
- `propstore/support_revision/scope_policy.py` (new) — the
  `@scope_policy(extract_from=, extract_step=, degrade=, require=)`
  decorator (see section 13 for the implementation)
- `propstore/world/model.py` — add `WorldQuery.at_journal_step`, `ClaimView`
  return type. Method decorated with `@scope_policy(...)` so scope-completeness
  policy is centralized, not inlined.
- `propstore/tests/test_scope_policy.py` (new) — unit tests for the decorator
  with stub functions covering all combinations of missing scope fields
- `propstore/tests/test_world_query_at_journal_step.py` (new) — hypothesis
  property test
- `propstore/tests/fixtures/journal.py` (new) — synthetic-journal /
  synthetic-sidecar fixtures
- `propstore/tests/conftest.py` — register new fixtures
- `propstore/propstore-narrative-review.md` — add "Resolution" section
  pointing at this design
- `propstore/TODO.md` — strike the three narrative-review-related items
  resolved by this work

**Acceptance criteria:**
1. **Property test passes.** Hypothesis-generated journals of 1-5 steps over
   a synthetic 10-claim sidecar produce equal `claim_ids` sets between direct
   sidecar execution and `at_journal_step` at every step. Run 100 random
   cases, all pass. (See section 11 for the test code.)
2. **Manual smoke test passes.** Build a hand-crafted 3-step journal from a
   fixture; assert `at_journal_step(j, k)` for k=0,1,2 returns claim sets that
   differ in the expected direction (revisions add claims; contractions
   remove claims).
3. **Stance-free Mara-Jade gate.** Construct a minimal HttE-like fixture: one
   chapter-1 worldline-step containing two claim atoms (`mara_learns_orders`,
   `mara_assigned_to_find_karrde`). Query `at_journal_step(j, 0)`. Assert
   correct claim membership. **If this gate requires stance projection that
   the minimal bridge cannot deliver, STOP and promote heavy variant
   (Phase 3) to Phase 1.** This is the explicit Q1 trigger.
4. **Documentation reconciliation.** `propstore-narrative-review.md` carries
   a "Resolution" header pointing here. `propstore/TODO.md` no longer lists
   "temporal validity" or "world.at(time)" as open items.

### Phase 2 — journal capture (~3-4 days, parallelizable with Phase 1)

**Goal:** ship `capture_journal` so the write direction (build a
`TransitionJournal` from a worldline run) actually produces journals
the bridge can read.

**Files to touch:**
- `propstore/support_revision/revision_capture.py` — add `capture_journal(
  bound, operations: Sequence[TransitionOperation]) -> TransitionJournal`
- `propstore/families/documents/worldlines.py` — extend
  `WorldlineDefinitionDocument` with optional `journal: TransitionJournal | None`
  field
- `propstore/contracts.py` — bump `DOCUMENT_SCHEMA_CONTRACT_VERSION` per
  existing convention (`propstore.worldline_definition.vN` increment); update
  `DOCUMENT_SCHEMA_CONTRACT_VERSION_OVERRIDES` mapping
- `propstore/families/registry.py` — keep `WorldlineDefinition` family name;
  add a registry comment documenting `WorldlineDefinition ≡ TransitionJournal-
  bearing belief space` (this is the Q3 resolution)
- `propstore/tests/test_worldline_journal_capture.py` (new) — exercise
  `capture_journal` end-to-end
- `propstore/tests/test_worldline_definition_backcompat.py` (new) — assert
  old single-revision documents still parse and round-trip
- `propstore/cli/worldline/__init__.py` + `propstore/cli/worldline/materialize.py`
  — add `pks worldline build-journal` and `pks worldline at-step`
  subcommands

**Acceptance criteria:**
1. `capture_journal((REVISE, REVISE, CONTRACT))` produces a 3-entry
   `TransitionJournal` whose `replay()` reproduces the same final state as
   sequential direct dispatch.
2. Backcompat: every existing test constructing a `WorldlineDefinition` passes
   without modification (`pytest propstore/tests/test_*worldline*.py`).
3. New schema: a `WorldlineDefinitionDocument` written with the journal field
   round-trips through `to_yaml`/`from_yaml`; one written without it (legacy
   shape) is still parseable.
4. CLI works: `pks worldline build-journal --from-source <fixture-slug>`
   produces a journal artifact; `pks worldline at-step <id> 1` returns
   expected claim set.

### Phase 3 — heavy variant (~1 week, CONDITIONAL)

**Trigger:** Phase 1 acceptance criterion #3 (stance-free Mara-Jade gate)
fails, OR fiction-curation Layer 8 implementation surfaces an inter-step
stance-projection requirement that the minimal bridge cannot serve.

**Goal:** add `at_journal_step(..., heavy=True)` that re-derives stances and
conflicts at step k by checking out `state.scope.commit` and rebuilding the
sidecar, then filtering.

**Files to touch:**
- `propstore/world/model.py` — extend `at_journal_step` with `heavy=False`
  parameter
- `propstore/world/journal_replay.py` (new) — checkout-and-rebuild logic plus
  cache
- `propstore/tests/test_at_journal_step_heavy.py` (new) — parity with
  non-heavy on stance-free journals; assert stances surface at heavy k

**Acceptance criteria:**
1. Heavy variant returns the same `claim_ids` set as non-heavy on stance-free
   journals.
2. Heavy variant additionally returns stance/conflict data; integration test
   on the minimal Mara-Jade fixture surfaces an inter-step stance.
3. Cache: hit rate measurable via `WorldQuery.heavy_cache_stats()`.

### Phase 4 — Cayrol classifier wiring (optional, ~1 day)

**Goal:** surface the existing Cayrol classifier as a journal-step UI annotation.

**Files to touch:**
- `propstore/cli/worldline/__init__.py` — add `pks worldline classify-step
  <id> <k>` subcommand
- Wire to existing
  `argumentation/src/argumentation/af_revision.py::_classify_extension_change`

**Acceptance criteria:** CLI returns one of decisive / restrictive /
questioning / destructive for argument-addition steps; returns "n/a" for
non-AF-extension steps.

### Phase 5 — federation handoff (downstream, out of scope here)

Per `quire/plans/distributed-layer-proposal-2026-05-02.md`. Journals become
exchangeable artifacts across repos. Cross-repo views compose by per-reader
projection over (local ∪ fetched-foreign-via-trusted-bridges); no global
merge. The bridge in this doc is a precondition.

### Phase 6 — cross-doc reconciliation (~2 hours, runs at end)

**Goal:** ensure no dangling references and bidirectional cross-doc links.

**Files to touch:**
- `propstore/propstore-narrative-review.md` — final sweep; mark "Causal edges"
  and "Subgraph pattern queries" as separately-tracked workstreams; mark
  "Temporal validity intervals" as RESOLVED via this design
- `propstore/TODO.md` — final pass on resolved items
- `propstore/AGENTS.md` — single paragraph if the worldline-as-journal reframe
  affects boundary rules; otherwise no change (decision in Phase 6, not
  pre-execution)
- `propstore/docs/git-backend.md` — branch-meta-as-ephemeral note (called out
  as a pain point in the round-1 propstore-merge-infra report) is partially
  resolved by journals as durable artifacts; document this
- `quire/plans/fiction-curation-schema-2026-05-02.md` — Layer 8 section update
  referencing the bridge as available
- `quire/plans/distributed-layer-proposal-2026-05-02.md` — federation section
  adds the bridge as a completed precondition reference

**Acceptance criteria:**
- No remaining "TODO: see narrative-review" references in propstore
- Cross-references between this doc, the schema doc, and the distributed-layer
  proposal are bidirectional
- `pks --help` lists the new worldline subcommands

---

## 11. Property suite — the bridge's contract

Propstore already uses hypothesis (verified: `tests/test_af_revision_postulates.py`,
`tests/conftest.py`). No new dependencies. Default settings: `max_examples=1000`
per property unless explicitly relaxed. CI must run the full suite; no skip
for hypothesis flakiness — flakiness on a property is itself a falsification.

Each property below has an identifier (P*). Per-phase acceptance criteria
(section 10) reference these identifiers. Adding code without a corresponding
falling property is forbidden by Phase 1 ordering.

### Fixtures

In `propstore/tests/fixtures/journal.py` (new), registered via `conftest.py`:

- `synthetic_sidecar(n_claims: int = 10) -> WorldQuery` — minimal
  in-memory sidecar with N known claim atoms in known state, backed by
  ephemeral SQLite via propstore's existing `connect_sidecar(...)` pattern.
- `synthetic_journal_strategy(*, steps, sidecar)` — hypothesis strategy
  generating `TransitionJournal` instances. Operations drawn from
  `{REVISE, CONTRACT, ITERATED_REVISE}`; targets drawn from sidecar's
  claim_ids.
- `synthetic_snapshot_strategy()` — generates `EpistemicSnapshot` directly
  for properties that don't need a journal context.
- `synthetic_scope_strategy(*, missing: set[str] = ())` — generates
  `RevisionScope` with named fields elided. Used by P-SCOPE-* properties.
- `direct_dispatch(sidecar, operations) -> WorldQuery` — applies
  operations sequentially via `support_revision.dispatch.dispatch`, returns
  the resulting view. The ground truth against which `at_journal_step` is
  validated.

### Phase 1 properties (must hold to merge Phase 1)

**P1 — `snapshot_to_claim_ids` is deterministic.**
```python
@given(snap=synthetic_snapshot_strategy())
def test_snapshot_to_claim_ids_deterministic(snap):
    assert snapshot_to_claim_ids(snap) == snapshot_to_claim_ids(snap)
```

**P2 — empty snapshot maps to empty claim set.**
```python
def test_empty_snapshot_yields_empty():
    snap = empty_snapshot()
    assert snapshot_to_claim_ids(snap) == set()
```

**P3 — accepted-atom claim_ids appear; unaccepted ones don't.**
```python
@given(snap=synthetic_snapshot_strategy())
def test_accepted_versus_unaccepted(snap):
    accepted = set(snap.state.accepted_atom_ids)
    result = snapshot_to_claim_ids(snap)
    for atom in snap.state.base.atoms:
        for claim in atom.source_claims:
            cid = str(claim.claim_id)
            if atom.atom_id in accepted:
                assert cid in result
            # unaccepted atoms may share source_claims with accepted ones;
            # the right invariant is: cid in result iff some accepted atom
            # has it as a source_claim
```

**P4 — many-to-one collapse: an atom with N source_claims contributes all N.**
```python
@given(snap=synthetic_snapshot_strategy().filter(has_multi_source_atom))
def test_many_to_one_collapse(snap):
    accepted = set(snap.state.accepted_atom_ids)
    expected = {
        str(c.claim_id)
        for atom in snap.state.base.atoms
        if atom.atom_id in accepted
        for c in atom.source_claims
    }
    assert snapshot_to_claim_ids(snap) == expected
```

**P5 — Dixon-shape behavioural equivalence (THE correctness property).**
```python
@given(journal=synthetic_journal_strategy(steps=integers(1, 5)))
def test_at_journal_step_matches_direct_dispatch(journal):
    sidecar = synthetic_sidecar()
    for k in range(len(journal.entries)):
        ground_truth = direct_dispatch(
            sidecar, journal.entries[:k+1]
        ).all_claim_ids()
        bridged = sidecar.at_journal_step(journal, k).claim_ids()
        assert ground_truth == bridged, (
            f"step {k}: missing={ground_truth - bridged}, "
            f"extra={bridged - ground_truth}"
        )
```

**P6 — step-bounds validation.**
```python
@given(journal=synthetic_journal_strategy(steps=integers(1, 5)))
def test_step_bounds(journal):
    sidecar = synthetic_sidecar()
    with pytest.raises(IndexError):
        sidecar.at_journal_step(journal, -1)
    with pytest.raises(IndexError):
        sidecar.at_journal_step(journal, len(journal.entries))
```

**P-PLS — narrowed Bonanno PLS frame property at the implementation level.**
```python
@given(pair=st_pls_pair())
def test_pls_diff_apply_roundtrip(pair):
    s, t, constructed_diff = pair
    diff = diff_epistemic_snapshots(s, t)
    assert constructed_diff.is_applicable_to(s)
    assert diff.is_applicable_to(s)
    assert apply_epistemic_diff(s, constructed_diff) == t
    assert apply_epistemic_diff(s, diff) == t
```
This realizes the PLS invariant Bonanno [2010] axiomatizes for the
normal-form transition surface propstore actually exposes:
identical-state-and-operation yields identical-state-out. The literal
independent-pair property over arbitrary `s` and `t` does not hold for
propstore's current `_apply_acceptance_delta` implementation because accepted
atom ids are rebuilt in sorted-key order; an adversarial target tuple with the
same accepted atoms in a different order is semantically equivalent for claim
projection but not structurally equal as an `EpistemicState`. P-PLS is
therefore the applicable-delta round-trip over targets constructed by applying
generated deltas to `s`. propstore's `apply_epistemic_diff` hash-validation
(`history.py:427-455`) is the concrete enforcement. The property checks the
frame law over this narrowed, order-normalized transition domain.

**P-SCOPE-DEGRADE — `@scope_policy(degrade=...)` degrades correctly.**
```python
@given(scope=synthetic_scope_strategy(missing={"bindings"}))
def test_scope_policy_degrades_on_missing_field(scope):
    snap = snapshot_with_scope(scope)
    journal = single_step_journal(snap)
    sidecar = synthetic_sidecar()
    with pytest.warns(UserWarning, match="degrading to rebind=False"):
        sidecar.at_journal_step(journal, 0, rebind=True)
    # Result must still be a valid claim view (the fallback is meaningful)
```

**P-SCOPE-REQUIRE — `@scope_policy(require=...)` raises correctly.**
```python
@given(scope=synthetic_scope_strategy(missing={"commit"}))
def test_scope_policy_raises_on_missing_required_field(scope):
    snap = snapshot_with_scope(scope)
    journal = single_step_journal(snap)
    sidecar = synthetic_sidecar()
    with pytest.raises(ValueError, match="missing.*commit"):
        sidecar.at_journal_step(journal, 0, heavy=True)
```

**P-SCOPE-NOOP — `@scope_policy` is a no-op when fields present.**
```python
@given(scope=synthetic_scope_strategy(missing=set()))  # all present
def test_scope_policy_noop_when_complete(scope):
    snap = snapshot_with_scope(scope)
    journal = single_step_journal(snap)
    sidecar = synthetic_sidecar()
    # No warning, no raise; behavior identical to undecorated call
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # any warning becomes a test failure
        view = sidecar.at_journal_step(journal, 0, rebind=True)
    assert view is not None
```

**P-MARA — stance-free Mara-Jade gate (Phase 1 trigger for heavy variant).**
```python
def test_mara_jade_stance_free_gate():
    sidecar = mara_jade_minimal_fixture()  # ch.1 with two known claim atoms
    journal = single_chapter_journal(
        revisions=[reveal_mara_orders, reveal_mara_assignment]
    )
    view = sidecar.at_journal_step(journal, 0)
    assert view.claim_ids() == {"mara_learns_orders", "mara_assigned_to_find_karrde"}
    # If this assertion needs heavy=True to pass, the minimal bridge is
    # insufficient for fiction Layer 8; promote Phase 3 to Phase 1.
```

### Phase 2 properties

**P-CAP-1 — `capture_journal` is deterministic.**
```python
@given(ops=hypothesis_operations(steps=integers(1, 5)))
def test_capture_journal_deterministic(ops):
    sidecar = synthetic_sidecar()
    bound = sidecar.bind(...)
    j1 = capture_journal(bound, ops)
    j2 = capture_journal(bound, ops)
    assert j1 == j2  # modulo timestamps if any; assert structural equality
```

**P-CAP-2 — replay equivalence: capture-then-replay matches direct dispatch.**
```python
@given(ops=hypothesis_operations(steps=integers(1, 5)))
def test_capture_replay_matches_direct(ops):
    sidecar = synthetic_sidecar()
    bound = sidecar.bind(...)
    j = capture_journal(bound, ops)
    replayed = j.replay()
    direct = direct_dispatch(sidecar, ops)
    assert replayed.final_state == direct.epistemic_state()
```

**P-CAP-3 — backcompat: legacy WorldlineDefinitionDocument round-trips.**
```python
@given(doc=legacy_worldline_definition_strategy())  # no journal field
def test_legacy_worldline_def_roundtrips(doc):
    yaml_text = doc.to_yaml()
    parsed = WorldlineDefinitionDocument.from_yaml(yaml_text)
    assert parsed == doc
```

**P-CAP-4 — new schema: WorldlineDefinitionDocument with journal round-trips.**
```python
@given(doc=journal_bearing_worldline_definition_strategy())
def test_journal_bearing_worldline_def_roundtrips(doc):
    yaml_text = doc.to_yaml()
    parsed = WorldlineDefinitionDocument.from_yaml(yaml_text)
    assert parsed == doc
    assert parsed.journal is not None
```

**P-CAP-5 — CLI parity: `pks worldline build-journal` + `pks worldline at-step`
matches in-memory.**

End-to-end property exercising the CLI subprocess and asserting equivalence
with the in-memory composition.

### Phase 3 properties (conditional)

**P-HEAVY-1 — parity on stance-free input.**
```python
@given(journal=stance_free_journal_strategy(steps=integers(1, 5)))
def test_heavy_parity_on_stance_free(journal):
    sidecar = synthetic_sidecar()
    light = sidecar.at_journal_step(journal, len(journal.entries)-1, heavy=False)
    heavy = sidecar.at_journal_step(journal, len(journal.entries)-1, heavy=True)
    assert light.claim_ids() == heavy.claim_ids()
```

**P-HEAVY-2 — heavy surfaces stances minimal does not.**
```python
def test_heavy_surfaces_stances():
    sidecar = sidecar_with_known_stance()
    journal = journal_referencing_that_stance()
    heavy = sidecar.at_journal_step(journal, 0, heavy=True)
    light = sidecar.at_journal_step(journal, 0, heavy=False)
    assert heavy.stances() != ()
    assert light.stances() == ()  # minimal variant does not project stances
```

**P-HEAVY-3 — cache stats reflect hits/misses correctly.**

### Phase 4 properties (optional)

**P-CAY-1 — Cayrol classification is one of the seven structural categories
or "n/a".**
```python
@given(journal=argument_addition_journal_strategy())
def test_cayrol_classification_in_known_set(journal):
    for k in range(len(journal.entries)):
        cls = classify_step(journal, k)
        assert cls in {
            "decisive", "restrictive", "questioning", "destructive",
            "expansive", "conservative", "altering", "n/a"
        }
```

### Phase 6 (docs reconciliation)

Not hypothesis-testable. Lint-shaped: a script `scripts/check_dangling_refs.py`
asserts no `narrative-review.md` references in propstore's TODO/AGENTS without
resolution markers. Runs in CI as a non-hypothesis test.

### Stickiness

Once any property above passes against N=1000 cases, it is the bridge's
public contract. Removing or weakening it requires committed rationale
referencing this doc. Adding properties is always allowed. The property
suite grows monotonically with bridge capability; its strength is the
bridge's strength.

---

## 12. CLI and documentation surface

### CLI subcommands (Phase 2 unless noted)

- `pks worldline build-journal --from-source <slug> [--out <id>]` —
  capture a journal from an existing source-extraction pipeline run
- `pks worldline at-step <id> <k> [--heavy]` — query the bridge
- `pks worldline classify-step <id> <k>` (Phase 4) — Cayrol category

Wire into `propstore/cli/worldline/__init__.py` per the existing CLI patterns
(`propstore/cli/worldline/{display,materialize,mutation,rendering}.py` are
already there; add to or create `query.py`).

### CHANGELOG / release notes

Propstore has no `CHANGELOG` file (verified). Per existing convention,
release-notable changes go in commit messages (visible via `git log`). Use
the existing commit-message format; no new file.

### Documentation files

Per Phase 6 acceptance criteria: `propstore-narrative-review.md`,
`TODO.md`, `AGENTS.md` (only if needed), `docs/git-backend.md`, plus the
two quire/plans/ docs. List is in the Phase 6 spec.

### Cross-doc links

This doc lives at `quire/plans/worldline-journal-bridge-2026-05-02.md`.
Three docs reference it as an upstream dependency:

- `quire/plans/fiction-curation-schema-2026-05-02.md` Layer 8
- `quire/plans/distributed-layer-proposal-2026-05-02.md` federation section
- `propstore/propstore-narrative-review.md` (after Phase 1)

---

## 13. Open questions (the actually-still-open ones)

The two questions resolved at design time are gone (Q1 heavy-variant trigger
is now an explicit Phase 1 acceptance gate; Q3 naming is now an explicit
Phase 2 directive: keep `WorldlineDefinition`, document equivalence in registry
comment).

One question remains genuinely open at design time, but its resolution is
fully specified — and ships as a reusable decorator rather than inlined
policy.

1. **Snapshot-without-scope handling.** Some `EpistemicStateSnapshot.scope`
   fields may be partially-populated for synthetic / test journals. The
   `rebind=True` and `heavy=True` paths need to handle missing scope fields
   without inlining branchy validation in every snapshot-consuming method.

   **Resolution: a `@scope_policy` decorator** in
   `propstore/support_revision/scope_policy.py`. Per-method declarative
   policy:
   - `degrade={kwarg: (required_fields,)}` — if the kwarg is truthy and any
     field is unset, force the kwarg to `False` and emit a warning. Use when
     the fallback gives a meaningful answer (e.g., `rebind=True → rebind=False`
     still returns a correct claim view).
   - `require={kwarg: (required_fields,)}` — if the kwarg is truthy and any
     field is unset, raise `ValueError`. Use when no fallback gives a
     meaningful answer (e.g., `heavy=True` without `scope.commit` — the
     entire point of `heavy` is rebuilding from that commit; silent fallback
     would mislead).

   Implementation:

   ```python
   # propstore/support_revision/scope_policy.py
   import functools, inspect, warnings
   from collections.abc import Mapping

   def scope_policy(
       *,
       extract_from: str,
       extract_step: str | None = None,
       degrade: Mapping[str, tuple[str, ...]] = (),
       require: Mapping[str, tuple[str, ...]] = (),
   ):
       degrade = dict(degrade) if degrade else {}
       require = dict(require) if require else {}

       def _missing(scope, fields):
           return [f for f in fields if not getattr(scope, f, None)]

       def decorator(func):
           sig = inspect.signature(func)
           @functools.wraps(func)
           def wrapper(*args, **kwargs):
               bound = sig.bind(*args, **kwargs)
               bound.apply_defaults()
               obj = bound.arguments[extract_from]
               if extract_step is not None:
                   obj = obj.entries[bound.arguments[extract_step]].state_out
               scope = obj.state.scope

               for kw, fields in require.items():
                   if bound.arguments.get(kw) and (m := _missing(scope, fields)):
                       raise ValueError(
                           f"{func.__qualname__}({kw}=True) requires "
                           f"snapshot.scope to have {fields}; missing: {m}"
                       )
               for kw, fields in degrade.items():
                   if bound.arguments.get(kw) and (m := _missing(scope, fields)):
                       warnings.warn(
                           f"{func.__qualname__}({kw}=True) requested but "
                           f"snapshot.scope is missing {m}; degrading to "
                           f"{kw}=False",
                           stacklevel=2,
                       )
                       kwargs[kw] = False
               return func(*args, **kwargs)
           return wrapper
       return decorator
   ```

   Usage on `at_journal_step`:

   ```python
   @scope_policy(
       extract_from="journal",
       extract_step="k",
       degrade={"rebind": ("bindings", "context_id")},
       require={"heavy": ("commit",)},
   )
   def at_journal_step(self, journal, k, *, rebind=False, heavy=False):
       # method body — scope-completeness already enforced
       ...
   ```

   Tested in isolation via `test_scope_policy.py` with a stub function and
   all field-missing combinations. When Phase 3 (heavy variant) or Phase 5
   (federation) adds more snapshot-bearing methods, they reuse the same
   decorator; policy stays centralized and named.

**Removed from open questions: belief_set IC-merge integration.** PAF IC
merge in `argumentation/partial_af` (Coste-Marquis 2007) already handles
within-repo branch merge and is propstore-wired. No use case for the
belief-base variant (`belief_set/ic_merge.py`, Konieczny-Pino-Pérez 2002)
was constructible within this bridge's scope — cross-repo federation
projects per-reader rather than merging into a global state. If a use case
surfaces (most likely candidate: a `belief_set` meta-analysis pipeline
consuming propstore snapshots, e.g., for an Ioannidis-style aggregator over
published findings), it gets its own workstream that builds the
`ps:assertion:* ↔ frozenset[str]` atom-id mapping. Not this bridge's
problem.

---

## 14. References

### Ingested in `propstore/papers/`
- Bonanno, G. [2007]. "AGM Belief Revision in a Temporal Logic". *Artificial
  Intelligence*. → branching-time AGM frames, K*1-K*8 axiomatization.
- Bonanno, G. [2010, 2012]. "Belief Change in Branching Time". *JPL*. →
  PLS frame property = AGM-consistency; ternary `B(h, K, φ)` iterated
  revision.
- Dixon, S. & Wobcke, W. [1993]. "The implementation of a first-order logic
  AGM belief revision system". → ATMS-into-AGM behavioural equivalence
  (Theorem 1).
- Halpern, J. & Pearl, J. [2000, 2005]. "Causes and Explanations: A
  Structural-Model Approach". → actual cause; already wired in
  `world/actual_cause.py`.
- Halpern, J. [2015]. "A Modification of the Halpern-Pearl Definition of
  Causality".
- Pearl, J. [2000]. *Causality: Models, Reasoning, and Inference*.

### Ingested in `argumentation/papers/`
- Baumann, R. [2015]. "AGM meets Abstract Argumentation: Expansion and
  Revision for Dung Frameworks". → AGM-on-AFs operators.
- Baumann, R. [2019]. "AGM Contraction on Dung Frameworks". → Harper
  Identity fails; revision/contraction asymmetric.
- Coste-Marquis, S. et al. [2007]. "Merging Dung's Argumentation Systems".
  → IC merging on Partial AFs, distance-based aggregation.
- Cayrol, C. et al. [2014]. "Change in Abstract Argumentation Frameworks:
  Adding an Argument". → single-argument delta classifier; already wired
  in `argumentation/af_revision.py`.
- Oikarinen, E. & Woltran, S. [2010]. "Characterizing Strong Equivalence
  for Argumentation Frameworks". → kernel-based equivalence; future cache
  dedup oracle.

### Ingested in `metanovel/papers/` (relevant downstream — fiction-curation)
- Cardona-Rivera, R. et al. [2012]. "Indexter". → narrative-time-indexed
  reference, salience formula.
- Bal, M. [1985]. *Narratology*. → text/story/fabula three-layer model
  (digests Genette).
- Trabasso, T. & van den Broek, P. [1985]. "Causal Coherence in Narrative".
- Mostafazadeh, N. et al. [2020]. "GLUCOSE". → causal commonsense
  annotations.

### External (must cite from literature, not ingested locally)
- Alchourrón, C., Gärdenfors, P., Makinson, D. [1985]. "On the Logic of
  Theory Change: Partial Meet Contraction and Revision Functions". *JSL*
  50(2). → AGM origin.
- Darwiche, A. & Pearl, J. [1997]. "On the Logic of Iterated Belief
  Revision". *AI* 89(1-2). → DP postulates; implemented in belief_set.
- Spohn, W. [1988]. "Ordinal Conditional Functions". → ranking-function
  semantics underlying belief_set's `SpohnEpistemicState`.
- Booth, R. & Meyer, T. [2006]. "Admissible and Restrained Revision".
  *JAIR* 26. → restrained revision; implemented in belief_set.
- Reiter, R. [1991]. "The Frame Problem in the Situation Calculus". → why
  fluents-over-time is hard; we duck the problem via discrete snapshots.
- Baltag, A., Moss, L., Solecki, S. [1998]. "The Logic of Public
  Announcements, Common Knowledge, and Private Suspicions". *TARK VII*. →
  action-model product update; semantic interpretation of
  `TransitionJournalEntry.operation`.
- Konieczny, S. & Pino-Pérez, R. [2002]. "Merging Information Under
  Constraints". *JLC* 12(5). → IC merge foundation; lifted by Coste-Marquis
  [2007] to AFs.
- Shapiro, S., Pagnucco, M., Lespérance, Y., Levesque, H. [2011]. "Iterated
  Belief Change in the Situation Calculus". *AI* 175(1). → most directly
  load-bearing prior bridge: revision operators integrated into a temporal
  action formalism. Our bridge is structurally analogous.
