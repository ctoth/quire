# Codex synthesis review - distributed layer

## Summary

- The round-1 synthesis is mostly grounded in the reports I read: the LINKING
  story, the "no DHT" conclusion, substrate/view separation, and quire vs
  propstore boundary all follow from the evidence.
- Its weakest move is saying propstore's current merge model **is** the
  academic-publishing model. It is compatible with that model, but it is not
  operationally that model yet.
- The three-axis frame is useful but incomplete. It hides an independent
  AUTHORITY axis: identity, signing, delegation, trust, acceptance policy, and
  schema governance.
- The synthesis is not ready to drive a concrete architecture proposal without
  the missing prior-art pass and a few human decisions.

Reports read: the synthesis first, then `propstore-merge-infra.md`,
`content-addressing.md`, `crdts-and-convergence.md`, `propstore-needs.md`,
`quire-shape.md`, `federation-protocols.md`, `forge-alternatives.md`, and
`git-annex.md`.

## Strongest Claim / Weakest Claim With Evidence

### Strongest claim: LINKING should use intrinsic IDs plus extrinsic hints, not a DHT

This is well grounded. The content-addressing report makes Software Heritage the
strongest evidence: git objects already have global intrinsic addresses, and the
working resolver model is centralized indexing over mirrorable content. The same
report says IPFS-style arbitrary CID-to-provider lookup has a long empirical
failure record, while out-of-band hints and centralized indexes actually work.

The git-annex report independently reinforces the shape: content-addressed keys,
side metadata, explicit providers, trust policy separated from mechanism, and
small remote verbs. The federation report agrees at a higher layer: stable ID plus
current-location resolution plus signed content is the repeated surviving pattern.

So the synthesis is right to reject "find this hash globally by DHT" and right to
prefer "verify intrinsically, locate extrinsically."

### Weakest claim: propstore merge **is** the academic-publishing model

This overreaches. The CRDT report's academic-publishing model is:

- per-writer append-only logs;
- selective publication events;
- citation as the durable substrate;
- no single converged state;
- reader-side trust/visibility projection.

Propstore's current merge implementation has a philosophical resemblance: it
preserves rival claims, classifies conflicts as attack/ignorance/non-attack, and
defers policy to render/query time. That part is honest.

But the current implementation is still single-repository, branch-centered, and
merge-commit-centered. It has no per-actor cryptographic identity, no transferable
publication object, no durable cross-repo citation surface, no multi-KB resolver,
no schema negotiation, and it hard-fails on `stances/`, the argumentation surface.
The merge object is currently an in-process/storage-local artifact, not an
academic publication record.

Better wording: propstore's "classify, don't resolve" merge is **compatible with**
the academic-publishing model and points in that direction. It is not yet that
model.

## Missing Prior Art

- **DataLad**: production scientific-data distribution on git-annex, with dataset
  hierarchies, provenance, lazy retrieval, and real scientific adoption.
- **Wikidata operational model**: claim-level statements with qualifiers,
  references, ranks, bots, community governance, and conflict tolerance at scale.
- **TerminusDB**: closest "git-like database over a knowledge graph" prior art;
  worth studying for revision semantics, schema, and graph identity failures.
- **Datomic**: immutable facts, time-aware queries, identity decoupled from
  mutable place, and an explicit tradeoff around the central transactor.
- **Production nanopublications / biomedical KGs**: nanopublications were mentioned,
  but production trust/indexing/query patterns need deeper treatment; ROBOKOP-like
  biomedical KGs are relevant for large-scale scientific claim integration.

Roam/Logseq block references are also worth a narrower pass because they attack
stable sub-document identity and transclusion, which maps directly onto
"address this claim/context/fragment, not just this file."

## Per-Question Short Answers

### Q3: Three-axis frame

The LINKING / COLLABORATION / KB-WEB TOPOLOGY split is useful, but it hides a
fourth axis: **AUTHORITY**.

Authority is not just a property of topology. It decides who can name a repo,
who can rotate keys, whose sameAs assertion counts, which external merge proposal
is admissible, which schema version is accepted, which source/repo is trusted,
and who can canonicalize a branch or KB view. The reports repeatedly surface this
under different names: Radicle delegates, atproto PLC/key rotation, git-annex
trust, propstore's missing per-actor identity, and sameAs authority. Treating it
as merely a topology feature will underdesign it.

### Q4: Academic-publishing reconciliation

Partly honest, but too strong.

The reconciliation is honest at the level of principle: both models preserve
rivals, avoid write-time truth resolution, and rely on later policy/projection.

It papers over a real implementation tension: academic publishing is built around
independent publication units and citation, while propstore today makes a local
two-parent merge commit into one repository and records a manifest. That is not
per-writer publication. It is branch convergence with semantic classification.

The architecture should keep the "classify, don't resolve" principle, but should
not assume current merge commits are the publication substrate.

### Q5: Repo identity

There is one clear negative answer: `urn:propstore:repository:<sha256-of-path>`
is wrong and should die.

Among the listed replacements, the best substrate default is a Radicle-style
key-derived repo identity backed by a signed identity document and rotation /
delegate log. `did:web` or `did:plc` can be useful human/discovery handles, but
they should be extrinsic locators, not the durable repo identity. A bootstrap-ref
SHA identifies genesis but does not by itself solve authority or rotation.

So this is not totally undecided. The human decision is the governance shape:
single repo key, delegate threshold, or some hosted authority. The identity core
should be key-derived and location-independent.

### Q6: Non-claim merge gap

Generalizing the **principle** of PAF-style classification is reasonable.
Generalizing the literal current claim PAF to every family is probably wrong.

`stances/` should merge as typed argumentation edges/statements with provenance,
target references, authorship, and conflict/ignorance classification. That is
close to the current semantic-merge model.

Other families need different reducers:

- concepts need identity/sameAs/replaced_by/rename semantics;
- forms and schemas need version compatibility gates;
- manifests need append-only or manifest-of-manifests handling;
- source and provenance records need authority-preserving merge;
- rules/contexts/lifting rules need explicit trust and scope policy.

The better pattern from prior art is typed append-only operations with
family-specific reducers: Radicle COBs for typed replicated objects, git-annex
per-actor union logs for gossip metadata, and Wikidata-style statement records
for assertion/qualifier/reference/rank surfaces. "Classify, don't resolve"
should remain the rule, but each family needs its own classification domain.

### Q7: Architecture readiness

Not yet. The synthesis is ready to define architecture questions and constraints,
not to choose the architecture.

Another research round should cover exactly the missing surfaces above:
DataLad/scientific git-annex use, Wikidata governance and statement model,
TerminusDB/Datomic/immutable DB prior art, block-reference systems, production
nanopublications and biomedical KGs, and distributed authorization/capability
systems. After that, architecture still needs human decisions on authority,
repo identity governance, and whether non-claim families are semantically merged
or forced through linear publication.

### Q8: Riskiest unstated assumption

The synthesis assumes preserving all rival artifacts plus provenance is enough
to make later projection tractable.

That may be false at "millions of agents" scale. The hard part may not be storage
or merge classification; it may be authority-weighted indexing, trust propagation,
schema drift, duplicate identity management, and query-time usability over a graph
full of contradictory, stale, low-quality, or adversarial assertions.

## Riskiest Unstated Assumption

The riskiest unstated assumption is that identity, trust, schema evolution, and
view/index economics can be layered after the storage substrate.

The reports show the opposite pattern. Systems that survive make authority and
view construction first-class early: Radicle has delegates and signed refs;
atproto has identity resolution plus AppViews; git-annex has trust and preferred
content; Wikidata-style systems need governance. If quire/propstore designs only
the object/link/merge substrate first, it may produce a technically clean graph
that nobody can safely accept, query, moderate, or operate.

## Verdict

**NEEDS-MORE-RESEARCH**

The round-1 synthesis is directionally strong, but the missing prior art is too
central to the target system. It should not drive a concrete architecture proposal
until the DataLad/Wikidata/TerminusDB-Datomic/nanopublication-biomedical/block-ref
and distributed-auth surfaces have been read, and until the repo-identity and
non-claim merge policy decisions are made explicitly.
