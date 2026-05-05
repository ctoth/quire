# Distributed layer for quire/propstore — proposal 2026-05-02

After 23 research/inventory reports across two rounds + one focused PrAF↔Jøsang
verification + adversary review + Codex external review + full propstore and
argumentation kernel inventories. The picture: most of what we'd need is
already built and currently unwired. This proposal names what to wire and what
small things to add.

## Summary

The distributed layer is **mostly a wiring exercise on existing standalone
modules in `~/code/argumentation/`**, plus a thin substrate shim in `quire`
and an atom-type taxonomy + labeler-subscription model in `propstore`.

Concretely: ~5 small additions in quire, ~8 small additions in propstore,
and integrating ~7 already-implemented argumentation modules that
propstore today does not consume. The existing 769-line `propstore/opinion.py`
(Jøsang subjective-logic algebra), the `propstore/context_lifting.py`
McCarthy/Guha bridge machinery, the `propstore/source_trust_argumentation/`
WS-K mapping, and propstore's existing per-claim provenance with
`branch_origin` are the load-bearing pieces — already there, already cited
in code against the right literature.

Anti-goal: this is NOT a federation protocol, NOT a DHT, NOT cross-repo
write-time merge, NOT a per-agent OIDC build-out. The shape is closer to
nanopublication-network + Bluesky-labelers + Wikidata-claim-rank composed
over content-addressed signed atoms with reader-side PAF projection.

## Goal

An agent should be able to:

1. Publish a signed propositional atom (claim, justification, stance,
   bridge, endorsement, replication, retraction) addressable by content hash.
2. Cite atoms from another agent's repo by `(content_hash, hint)`. No
   coordination with the other agent required.
3. Subscribe to a labeler — a curated set of trust opinions and lifting
   rules — and have the subscription's opinions automatically discount
   the cited atoms when running the local PAF.
4. Run reasoning over (local atoms ∪ fetched foreign atoms ∪ subscribed
   bridges) under their own integrity constraints, getting calibrated
   Jøsang opinions on conclusions that account for source reliability,
   curation fidelity, and field-level Ioannidis base rates.
5. Locally merge intentionally-coordinated branches using propstore's
   existing `RepositoryMergeFramework` — unchanged.

What the agent does NOT need to do:
- Negotiate identity with anyone.
- Coordinate writes with anyone.
- Trust a central registry by default.
- Migrate schemas (lifting rules translate context-locally).
- Resolve cross-repo conflicts (rivals coexist; reader's PAF projects).

## Architecture in one paragraph

Each propstore deployment is a sealed local KB exactly as today, with the
added capability of fetching foreign signed atoms by content hash and
storing them under a quire-private ref namespace. Foreign atoms live in
their own McCarthy contexts and do not interact with local atoms unless an
authored, signed `LiftingRule` bridges them. Trust over foreign atoms,
bridges, and authors composes via the existing `propstore/opinion.py`
Jøsang operators (`discount`, `wbf`, `ccf`, `consensus`). Reader-side
projection runs the PAF — using `argumentation.partial_af.{sum,max,leximax}_
merge_frameworks` for cross-repo argument expansion and
`argumentation.dynamic.parse_update_stream` for streaming foreign-atom
ingestion — over (local ∪ foreign-via-trusted-bridges) under the reader's
chosen integrity constraint. The result: calibrated Jøsang opinions on
conclusions, with full provenance back through `(local_claim →
bridge_author → foreign_claim → foreign_curator → foreign_source)`. No
write-time global merge ever happens; convergence is opt-in and local.

## The atom catalog

Eight signed-atom types. Some exist in propstore today (often unmarked as
their own type); some are new explicit families. Each is content-addressed
(RFC 6920 ni-URI; propstore has this in `propstore/uri.py` already), signed
by author, carries a `ProvenanceWitness`.

| Atom type | Today | Status |
|---|---|---|
| **Source assertion** | `families/sources/` (`SourceDocument`) | EXISTS — already explicit. Add cryptographic signature on the atom itself, beyond git commit signing. |
| **Curation assertion** | conflated with source today via `ProvenanceWitness.{source_paper, source_page, branch_origin}` | NEW EXPLICIT FAMILY. Separates "this curator extracted this claim from this source at this location" from "this source said X." |
| **Endorsement** | partial via `Source.trust` (paper-scoped) | NEW EXPLICIT FAMILY. Per-(author, source, domain, role) Jøsang opinion. |
| **Replication** | absent | NEW. First-class replication-attempt record: `(source, attempted_method, result, replicator, opinion)`. |
| **Retraction** | absent (git revert exists at commit level) | NEW. Asymmetric: author-self-retraction vs third-party counter-claim are different atoms. |
| **Lifting rule** | `propstore/context_lifting.py` `LiftingRule(BRIDGE/SPECIALIZATION/DECONTEXTUALIZATION)` | EXISTS. Add cryptographic signature + portable identity. |
| **Argumentation atom** | `families/claims/`, `families/justifications/`, `families/stances/` | EXISTS. Add cryptographic signature; extend `branch_origin` to optionally carry `(remote_repo_hint, foreign_commit_sha)`. |
| **Labeler manifest** | absent | NEW. Signed bundle of `(author_key, role, domain, opinion)` and `(LiftingRule, opinion)` tuples that subscribers import as a single trust delegation. |

## Cross-repo flow (the mechanism)

End-to-end: Alice wants to read Bob's claim about Smitherson 2018.

1. Alice has a citation: `(content_hash=H, hint=https://bob.example/.../H)`.
2. Alice's quire: lazy `BaseObjectStore.get_raw(H)` misses → consults
   location-hint registry → fetches from `hint` → verifies `H` against
   bytes → stores in local objects under `refs/quire/atoms/H`.
3. Alice's propstore: deserializes the atom, identifies it as a curation
   atom (Bob's curation of Smitherson). Verifies Bob's signature against
   Alice's known author-key registry (or labeler-imported registry).
4. The atom carries:
   - `provenance.source = Smitherson 2018 atom hash`
   - `provenance.curator = Bob's key`
   - `provenance.context = Bob's context-id`
   - `claim_payload = the actual extracted claim`
5. Alice's local context does NOT conflict with Bob's context unless
   Alice has a signed `LiftingRule` bridging them. By default the atom
   sits in Alice's repo as a foreign-context observation.
6. Alice runs reasoning: `propstore.opinion.discount(alice_opinion(bob_as_curator),
   bob.opinion(smitherson_assertion))` per `propstore/opinion.py:Def 14`.
   Already implemented.
7. Result: Alice's calibrated Jøsang opinion on "Smitherson said X."
   Reusing `propstore/preference.py:claim_strength` + the existing PrAF.
8. Cross-repo argument expansion when needed: invoke
   `argumentation.af_revision.baumann_2015_kernel_union_expand` (currently
   STANDALONE — wire it). Or for streaming foreign-atom ingestion,
   `argumentation.dynamic.parse_update_stream` (also currently STANDALONE).

No write-time coordination happened. No cross-repo merge happened. Alice's
local store grew by one atom (the foreign one) plus zero local-atom
mutations.

## Completed Precondition: Worldline Journal Bridge

The `WorldQuery` / `TransitionJournal` bridge described in
`plans/worldline-journal-bridge-2026-05-02.md` is now available in propstore.
That bridge gives federation readers a durable temporal projection primitive:
`pks worldline build-journal` captures a worldline trajectory, and
`pks worldline at-step NAME STEP` projects the accepted claim view at a
journal step.

For this distributed-layer proposal, that means read-time projection can assume
there is a concrete local API for "what did this repo accept at step k?" when a
foreign or local atom stream is represented as a worldline journal. It does not
provide cross-repo fetching, signatures, labeler subscription, or historical
stance/conflict re-derivation; those remain in the phases below.

## Trust composition (the labeler model)

A **labeler manifest** is a signed atom containing:
```yaml
labeler_id: <hash>
author: <key>
issued: <timestamp>
trusts:
  - {author_key: K1, role: curator, domain: biomedical, opinion: {b: 0.8, d: 0.0, u: 0.2, a: 0.5}}
  - {author_key: K2, role: source, domain: clinical_trials, opinion: {b: 0.9, d: 0.05, u: 0.05, a: 0.7}}
bridges:
  - {lifting_rule_hash: L1, opinion: {b: 0.7, d: 0.0, u: 0.3, a: 0.5}}
revocations:
  - {atom_hash: R1, reason: "retracted upstream 2026-04-15"}
```

Subscribing to a labeler = importing its `trusts` and `bridges` into your
local trust graph as **one delegated trust step**. When you read an atom,
your local opinion of it composes via `propstore.opinion.discount`:

```
your_opinion(claim) = discount(your_opinion(labeler), labeler_opinion(curator)) ⊗ curator_opinion(source) ⊗ source_opinion(claim)
```

That's `discount` chained per `propstore/opinion.py` Def 14 — implemented.
For multi-source corroboration, `consensus` (Jøsang Theorem 7), `wbf`
(van der Heijden Def 4), or `ccf` (Def 5) — all implemented in the same
file.

Subscribing to multiple labelers: their per-(author, role, domain) opinions
fuse via `wbf` or `ccf` depending on whether the labelers are independent
sources of the same trust evidence (cumulative) or weighted authorities
(weighted).

This **is** the Bluesky labeler pattern with formal Jøsang semantics
underneath.

## Wiring map (what to consume from argumentation/)

Each line is "currently STANDALONE in argumentation kernel; consume from
propstore at this trigger":

| Module | Trigger | Propstore call site |
|---|---|---|
| `argumentation.dynamic.parse_update_stream` / `apply_update_stream` | Foreign atoms arrive (firehose-style consumption) | New: `propstore.federation.foreign_atom_stream` |
| `argumentation.af_revision.baumann_2015_kernel_union_expand` | Reader requests "include foreign arguments in my AF" | New: `propstore.federation.expand_local_af_with_foreign` |
| `argumentation.af_revision.baumann_2015_kernel` (K1-K6) | Local AF needs revision when foreign arguments contradict local commitments | Same call site, semantics-aware dispatch |
| `argumentation.enforcement.enforce_expansion_credulous` / `_skeptical` | Reader asks "given foreign atoms, can my preferred conclusions still hold?" | New: `propstore.federation.check_conclusion_preserved` |
| `argumentation.partial_af.{sum,max,leximax}_merge_frameworks` | Cross-repo IC merge (currently used by `propstore.merge.structured_merge` for local merge ONLY — extend to cross-repo) | Existing call site, extend with foreign branch_origins |
| `argumentation.caf` (Claim-Augmented AF) | When reasoning over claim-level granularity (which propstore IS — every claim is a CAF claim) | DECISION POINT — see Open Decisions |
| `argumentation.epistemic` (Hunter-Polberg-Thimm) | If/when reader wants higher-precision belief than Jøsang `(b,d,u,a)` for a specific query | Optional; do not wire by default |
| `argumentation.dynamic.IncrementalDynamicArgumentationFramework` | Long-running reader sessions consuming foreign-atom streams | New: `propstore.federation.live_view` |

These are seven concrete wiring tasks. Each is ~50-200 lines of glue code
that constructs the right argumentation-kernel inputs from propstore's
existing data and surfaces the result back through the existing
`PropstorePrAF` machinery (which is already opinion-valued per the
PrAF↔Jøsang verification report).

## Quire-side additions (small)

Quire stays a generic typed-Git/document substrate per `AGENTS.md`. Five
small additions:

1. **Lazy `BaseObjectStore` subclass** — extends `dulwich.object_store.OverlayObjectStore`
   with on-miss `get_raw(sha)` that consults a location-hint registry and
   fetches via existing dulwich transport. Verifies hash before storing.
   `~150 lines.`

2. **Location-hint registry** — typed config: a list of
   `(content_hash_pattern, transport_url)` rules. One entry per known
   foreign repo. Persisted to `refs/quire/location-hints/<id>` as a JSON
   blob ref (the same pattern propstore already uses for branch metadata).
   `~100 lines.`

3. **`refs/quire/atoms/` namespace convention** — new ref namespace for
   foreign atoms fetched in. Exposed via `GitStore.read_atom(hash) ->
   bytes` and `GitStore.iter_atoms(prefix=)`. Just a thin wrapper over
   existing blob-ref machinery. `~50 lines.`

4. **`ArtifactAddress` extension** — optional `(repo_hint, expected_commit)`
   pair. When present, address dereferences via cross-repo fetch instead
   of local `branch_sha`. Backward compatible. `~30 lines.`

5. **Sigstore/gitsign verification helper** — wraps the existing
   `dulwich` commit-signature support with policy: "this commit was signed
   by a key in my trusted-author list at the time the commit was made
   (per labeler subscription)." `~100 lines.`

Total quire-side: ~430 lines of glue, no new dependencies beyond what
propstore already brings (Jøsang-aware modules already exist; signature
verification uses dulwich + existing sigstore tooling).

## Propstore-side additions

1. **Three new explicit atom families**: `families/curations/`,
   `families/endorsements/`, `families/replications/`, `families/retractions/`,
   `families/labelers/`. Each follows the existing `FamilyDefinition` pattern
   in `families/registry.py`. Per the inventory, that registry already has 26
   families; this adds 5. `~600 lines total across the families.`

2. **`propstore.federation` package** — new package containing:
   - `foreign_atom_stream.py` — wires `argumentation.dynamic.parse_update_stream`
   - `expand_local_af_with_foreign.py` — wires
     `argumentation.af_revision.baumann_2015_kernel_union_expand`
   - `check_conclusion_preserved.py` — wires
     `argumentation.enforcement.enforce_expansion_*`
   - `live_view.py` — wires
     `argumentation.dynamic.IncrementalDynamicArgumentationFramework`
   - `cross_repo_address.py` — extends `ArtifactAddress` resolution to
     dereference foreign refs via the new quire primitive
   - `labeler_subscription.py` — labeler import/refresh logic; populates a
     local trust graph from labeler manifests
   `~1200 lines total.`

3. **`ProvenanceWitness` extension** — `ProvenanceWitness` already carries
   `source_artifact_id, source_paper, source_page, branch_origin, rule_chain`
   per `propstore/merge/witness.py:10`. Add optional `(remote_repo_hint,
   foreign_commit_sha, labeler_chain)`. `~50 lines + migration.`

4. **Ioannidis FDR hook** — per-field FDR table (initially: Bird 2021's
   numbers) plus a small lookup at `propstore/praf/engine.py:p_arg_from_claim`
   that uses field-FDR as the prior `Opinion.a` when no explicit source-prior
   base rate is given. `~80 lines + a YAML data file in `_resources/`.`
   **Publishable contribution per the PrAF↔Jøsang verification report.**

5. **Make signing first-class on the new atom types** — every new family
   document carries a `signature: SigstoreOrSshSignature` field. Verification
   uses the quire helper. `~30 lines per family + key-registry plumbing.`

6. **CLI surface for cross-repo operations** — extend `pks` with:
   - `pks atom fetch <hash> [--hint URL]`
   - `pks labeler subscribe <hash> [--hint URL]`
   - `pks labeler refresh <id>`
   - `pks federation status` (which atoms are fetched, which labelers
     subscribed, current trust graph summary)
   `~300 lines across `cli/` per the existing CLI patterns.`

7. **Atom-type taxonomy migration helper** — for existing propstore
   deployments, a one-shot CLI that lifts existing
   `(provenance.source_paper, provenance.branch_origin)` tuples into
   explicit Source + Curation atoms. Idempotent. `~200 lines.`

8. **Documentation** — `docs/distributed-layer.md` capturing this design
   for the propstore project itself. `~1500 words.`

Total propstore-side: ~2500 lines + ~1500 doc words.

## What we are explicitly NOT building

- **A DHT.** Empirical 15-year track record of failure for global
  content-by-hash lookup. We use out-of-band hints + intrinsic verification.
- **Cross-repo write-time merge.** The merge machinery in
  `propstore.merge.*` stays exactly as-is for *local* coordinated work.
  Cross-repo is fetch + lift + read-time PAF projection. No global converged
  state ever exists.
- **A federation protocol.** No ActivityPub, no protocol negotiation, no
  inbox push. Each repo is a sealed local KB that exposes signed atoms.
  Other repos fetch by hash. That's it.
- **Per-agent OIDC accounts.** Identity is a key. Sigstore-style ephemeral
  certs bound to OIDC are fine for human curators; agents use stable keys
  with rotation logs.
- **Cross-repo schema migration.** Schemas are context-local. Lifting
  rules translate. If two repos disagree on schema, neither is "wrong" —
  they're in different contexts and need a bridge to interact.
- **A central registry of repos / authors / labelers.** Discoverability
  is a SEARCH problem (someone can build an index — Software Heritage
  proves this works), not an identity problem.
- **Exposing all 9 PrAF strategies.** Pick one (`exact_dp` for small
  graphs, `mc` with Agresti-Coull for large) and hide the menu. The
  argumentation kernel implements all 9 today; we don't need to expose
  them.

## Phasing

Each phase is independently shippable and adds user-visible value.

**Phase 1 — Atom-type taxonomy (~1 week).**
Define the 5 new families in `propstore/families/`. Add Sigstore signature
support. Ioannidis FDR hook in `praf/engine.py`. Migration helper for
existing data. Ship the Ioannidis-as-Jøsang-`a` paper (publishable
contribution). **Zero cross-repo capability yet.** This is the foundation.

**Phase 2 — Quire substrate (~1 week, parallel with Phase 1).**
Lazy `BaseObjectStore` subclass + location-hint registry + `refs/quire/atoms/`
namespace + `ArtifactAddress` extension + signature verification helper.
**Read-only foreign atom fetch works.** No reasoning over them yet.

**Phase 3 — Read-time projection over foreign atoms (~1 week).**
Wire the existing argumentation modules: `partial_af` extension to span
foreign branch_origins; `propstore.federation.expand_local_af_with_foreign`;
`propstore.federation.check_conclusion_preserved`. Reader can now reason
over (local ∪ fetched-foreign) under their chosen lifting rules. Temporal
claim-membership reads can use the completed worldline-journal bridge when the
foreign view is journal-shaped. **Cross-repo PAF projection works end-to-end.**

**Phase 4 — Labeler subscription (~1 week).**
Labeler manifest atom-type. Subscription import. Trust-graph composition
via `opinion.discount`/`wbf`/`ccf`. Subscribers inherit a curated trust set
in one delegated step. **Trust scales — no longer requires per-reader
hand-curation.**

**Phase 5 — Streaming foreign-atom consumption (~1 week).**
Wire `argumentation.dynamic.parse_update_stream` and
`IncrementalDynamicArgumentationFramework`. Long-running reader sessions
update their views as new foreign atoms arrive. **The "agent web" vision
is operational.**

Total: ~5 weeks of focused engineering for the full stack. Phases 1-2 are
parallelizable; 3-5 are sequential (each builds on the previous).

## Open decisions for Q

These cannot be answered by more research. They are governance/aesthetic
choices Q owns.

1. **Repo identity governance.** Codex was clear: delete the
   `urn:propstore:repository:<sha256-of-path>` placeholder. Replace with
   key-rooted identity. Open: single signing key, threshold-of-delegates
   (Radicle pattern), or external authority (did:plc-style)? My
   recommendation: **single key per repo for now, with a documented
   upgrade path to threshold-of-delegates.** Most repos will have one
   primary curator; multi-delegate is overengineering until needed.

2. **CAF or no CAF.** Propstore is a claim-level KB. The
   `argumentation.caf` module implements claim-augmented AF semantics
   (Dvorak/Rapberger/Woltran 2020-2023) directly designed for this case.
   Currently propstore uses Dung-AF semantics with stance-vocabulary
   classifiers projecting onto Dung. Switching to CAF would be more
   formally clean but would touch a lot of consumer code. My
   recommendation: **postpone — keep current Dung+stances, revisit if
   real-use friction surfaces.**

3. **PrAF strategy default.** Of 9 strategies in
   `argumentation.probabilistic`, only `compute_probabilistic_acceptance`
   is currently consumed (which dispatches to `auto`). For cross-repo
   work the candidate strategies are `exact_dp` (deterministic, scales
   to ~50 args) or `mc` with Agresti-Coull (stochastic, scales further).
   My recommendation: **`exact_dp` default, `mc` opt-in for large
   federated views.** Document the choice explicitly.

4. **Labeler index hosting.** Where does a reader DISCOVER labelers?
   - Option A: nothing — readers exchange labeler hashes manually
     (like sharing an RSS feed URL). Ship this in phase 4.
   - Option B: a Software-Heritage-style centralized directory
     (`labelers.propstore.org`?) that indexes published labeler manifests.
   - Option C: each repo publishes its own list of "labelers I trust"
     and discovery is by-citation through the trust graph.
   My recommendation: **Option A for phase 4; Option C as natural
   extension; Option B only if community demand justifies the operations
   burden.**

5. **The Ioannidis paper.** Per the PrAF↔Jøsang verification, operationalizing
   field-FDR as Jøsang `a` is novel territory with no prior art. Worth
   writing up as a paper independently of the propstore implementation.
   My recommendation: **draft outline parallel to phase 1 implementation;
   submit when phase 1 ships.**

## Why this is the right shape

Three threads converge:

1. **What worked at scale**: Wikidata claim-rank coexistence + Biolink-style
   structured provenance + nanopub-style content-addressed signed atoms.
   Each is in production at million+ scale. None requires write-time global
   merge. This proposal does the same.

2. **What propstore already built**: Jøsang algebra with provenance,
   McCarthy/Guha context lifting, RFC 6920 ni-URIs (content addressing),
   per-claim provenance with branch_origin, the full PRAF wrapper that's
   already opinion-valued. The "distributed layer" is largely lifting
   propstore's local-merge sophistication to cross-repo scope.

3. **What argumentation/ already built**: streaming-update consumption
   (`dynamic`), AF expansion under foreign-argument arrival (`af_revision`),
   expansion enforcement to check conclusion preservation (`enforcement`),
   IC merge operators (`partial_af`). Each module averages 400-700 lines
   and is currently STANDALONE. Wiring them is straightforward.

The mistake the original research swarm made was treating "distributed
layer" as a build-from-scratch problem. The actual problem is wiring +
small additions. The architecture is overwhelmingly already there.

## What I want from Q before any code is written

Decisions on the five open items above. Once those are nailed, phases 1
and 2 can start in parallel, and the whole stack is shippable in ~5 focused
weeks.

If Q wants to push back on any of the shape (atom catalog, anti-goals,
phasing), now is the time. After implementation begins the cost of reframe
goes up sharply.
