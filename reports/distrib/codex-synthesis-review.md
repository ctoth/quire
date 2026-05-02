# Codex synthesis review - distributed layer

## Summary

- The round-1 synthesis is factually strong where it stays close to the reports:
  quire has a clean storage seam, propstore has a real claim-only semantic merge,
  and intrinsic IDs plus extrinsic location hints are the right LINKING primitive.
- It overreaches when it turns analogies into reconciliations. The worst case is
  the claim that propstore's merge model is the academic-publishing model.
- The three-axis frame is useful for conversation but incomplete for architecture:
  TRUST/AUTHORIZATION and TIME/CONSISTENCY are not optional subtopics.
- The missing prior art is not peripheral. DataLad, Wikidata, TerminusDB/Dolt/Irmin,
  block references, and biomedical/nanopub operations directly stress the proposed
  shape.
- Verdict: NEEDS-MORE-RESEARCH before architecture, plus human decisions on repo
  identity, schema-version policy, and write-time merge vs per-writer publication.

Reports read: the synthesis first, then `propstore-merge-infra.md`,
`content-addressing.md`, `crdts-and-convergence.md`, `quire-shape.md`,
`propstore-needs.md`, `forge-alternatives.md`, `federation-protocols.md`,
`git-annex.md`, and the gap-focused round-2 reports on TerminusDB/graph systems,
scholarly KGs, DataLad, block refs, capabilities/auth, and mass collaboration.

## Strongest claim / weakest claim with evidence

### Strongest claim: intrinsic IDs plus extrinsic hints

The strongest claim is that LINKING should separate intrinsic verification from
extrinsic location. This is well grounded across independent reports.

`content-addressing.md` makes Software Heritage the decisive example: git
objects already have globally meaningful intrinsic identifiers, and a centralized
resolver over mirrorable content works at planet scale. The report also gives
the useful failure boundary: arbitrary global hash-to-provider discovery is the
bad IPFS-shaped question; known-swarm or hinted retrieval is different.

`git-annex.md` independently supports the same split. Its keys verify content;
side metadata says who has it; trust and preferred-content policy decide whether
to use it. `federation-protocols.md` reaches the same pattern at the social-web
layer: stable ID, current-location resolution, signed content, and separate views.

So the synthesis is right to prefer "verify intrinsically, locate extrinsically"
and right to resist making URLs or local paths canonical identity.

### Weakest claim: propstore merge IS academic publishing

The weakest claim is the synthesis's reconciliation that propstore's "classify,
don't resolve" merge is the academic-publishing model from the CRDT report.
That is not supported by the reports.

The CRDT report's academic-publishing model means per-writer append-only logs,
selective publication, citation as substrate, no single global converged state,
and reader-side trust/visibility projection.

Propstore today is different. `propstore-merge-infra.md` shows a single-rooted
`Repository`, mutable branches, two-parent merge commits, branch-keyed rival
materialization, and a `merge/manifest.yaml`. That is write-time convergence
inside one repository. It preserves disagreement, which is valuable, but it is
not per-writer publication and it is not citation-as-substrate. The same report
also shows no multi-KB resolver, no per-actor cryptographic identity, no schema
negotiation, and hard failure outside `claims/`.

The honest statement is narrower: propstore's current merge principle is
compatible with an academic-publishing architecture. It is not already that
architecture.

## Missing prior art

- **DataLad**: production scientific distribution on git-annex. It proves lazy
  retrieval and dataset hierarchies work, but also shows hard scaling limits,
  submodule contention, and weak provenance verification.
- **Wikidata operational model**: the closest production system for claim-level
  coexistence at scale: statements, qualifiers, references, rank, bot approval,
  advisory constraints, revert workflows, and human governance.
- **TerminusDB / Dolt / Irmin**: direct prior art for git-like data systems.
  TerminusDB shows triple-diff limits, Dolt shows queryable structural conflict
  tables, and Irmin shows per-type merge as the right abstraction boundary.
- **Roam/Logseq/Hypothes.is block references**: stable sub-document identity is
  a separate problem from repository or file identity. Claim-level links need
  block/selector thinking, not only commit/path thinking.
- **Biomedical KGs and production nanopublications**: ROBOKOP/Translator/Biolink
  show schema plus structured provenance in a real scientific federation; nanopubs
  show immutable assertion publication with read-time reconciliation.

## Per-question short answers

### Q3: Three-axis frame

LINKING / COLLABORATION / KB-WEB TOPOLOGY is a useful first cut, but it hides
two axes.

First, **TRUST/AUTHORIZATION** is independent. It decides who may name a repo,
rotate keys, update refs, submit merge proposals, assert sameAs, mark a schema
compatible, or canonicalize a view. Radicle delegates, atproto PLC, git-annex
trust, gittuf/TUF, Wikidata bot flags, and propstore's missing actor identity
are all evidence that this cannot be an afterthought.

Second, **TIME/CONSISTENCY** is independent. Readers need freshness semantics;
revocations need propagation; merge proposals need a base observation; gossip
logs need compaction; signed reference state needs ordering. KB-WEB topology is
better treated as the emergent result of the other axes, not as a peer design
surface.

### Q4: Academic-publishing reconciliation

The reconciliation is honest only at the slogan level: both models preserve
rivals and defer truth policy.

It papers over a real architecture fork. Propstore's current path is write-time
semantic merge into a shared DAG. Academic publishing is independent publication
plus citation plus reader-side projection. Those can be made to interoperate,
but they are not the same mechanism. Architecture must choose whether to extend
propstore's merge commits across repos or to move the distributed layer toward
per-writer publication logs and projections.

### Q5: Repo identity

There is a clear negative decision: `urn:propstore:repository:<sha256-of-path>`
must be deleted. It is not a durable identity.

The best default from the reports is location-independent and key-rooted: a
Radicle-style repo identity derived from a signed identity document, with
delegates or rotation recorded in a signed log. A bootstrap-ref SHA is useful as
genesis evidence but does not solve rotation or authority. `did:web` and
`did:plc` are useful handles/resolvers, but making either the durable identity
imports their operator and governance assumptions. So the technical direction is
fairly clear; the undecided part is governance: single key, threshold delegates,
or external authority.

### Q6: Non-claim merge gap

Generalizing the principle of PAF-style classification is reasonable.
Generalizing the literal claim merge to every family is not.

`stances/` should become typed, provenance-bearing argumentation records with
merge semantics, because stances are part of the argumentation surface. But
concepts need sameAs/rename/replaced_by semantics; contexts and lifting rules
need scoped authority; schemas need compatibility gates; manifests need
append-only or manifest-of-manifests handling; provenance records need source
authority. The better pattern is Irmin-style per-family merge contracts plus
Wikidata/nanopub-style statement records: preserve typed rivals, classify them
in the family's own domain, and expose conflicts as queryable data.

### Q7: Architecture readiness

Not ready. The synthesis is ready to define constraints and decision points,
not to drive a concrete architecture.

Another round should specifically cover: production scientific distribution
(DataLad/OpenNeuro/RIA stores), production claim-governance systems
(Wikidata/Lean/mathlib), git-like graph/database systems (TerminusDB, Dolt,
Irmin, Datomic/XTDB), stable block/fragment identity, biomedical KG provenance
and normalization, nanopub server operations, and distributed authorization
(TUF/gittuf/Sigstore/DID rotation/capabilities). Several of those reports now
exist and they materially change the verdict.

### Q8: Riskiest unstated assumption

The synthesis assumes that once storage preserves rivals with provenance, later
projection will be tractable. That is the dangerous assumption.

At the intended scale, the hard part may be duplicate identity, trust-weighted
indexing, schema drift, stale assertions, adversarial agents, and operator
economics. A substrate that can faithfully store every contradiction can still
be unusable if no one can decide what to fetch, trust, rank, compact, or show.

## Riskiest unstated assumption

The riskiest unstated assumption is that authority, schema evolution, and view
construction can be layered on after the Git/document substrate.

The prior art says the opposite. Radicle has delegates and signed ref state;
atproto has identity plus AppViews; git-annex has trust and preferred content;
Wikidata has ranks, constraints, bots, and human escalation; biomedical KGs have
schema and provenance discipline. These are not UI details. They determine
whether distributed writes are admissible and whether distributed reads are
usable.

## Verdict

**NEEDS-MORE-RESEARCH**

Round 1 is a strong landscape survey, but it is not architecture-ready. The next
architecture pass should start only after the hard decisions are explicit:
write-time cross-repo merge vs per-writer publication logs, repo identity
governance, lockstep schema versions vs migration/negotiation, and per-family
merge contracts for non-claim surfaces.
