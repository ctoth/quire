---
title: "Rondo: A Programming Platform for Generic Model Management"
authors: "Sergey Melnik; Erhard Rahm; Philip A. Bernstein"
year: 2003
venue: "SIGMOD 2003"
doi_url: "https://doi.org/10.1145/872757.872782"
pages: 12
---

# Rondo: A Programming Platform for Generic Model Management

## One-Sentence Summary
Rondo gives quire a concrete generic model-management platform: models as directed labeled graphs, morphisms as binary relationships between model elements, selectors as node sets, and model-at-a-time operators for change propagation, extraction, matching, merging, and view reuse. *(p.1-p.12)*

## Problem Addressed
Metadata applications repeatedly manipulate schemas, SQL views, XML schemas, mappings, and interface definitions; without a generic programming platform, developers reimplement object-at-a-time transformations and GUI-heavy glue for every application. The paper asks how far generic operators can carry metadata management while still allowing human feedback where matching and conflict resolution are inherently heuristic. *(p.1, p.2)*

## Key Contributions
- Defines the Rondo prototype and its conceptual structures: **models**, **morphisms**, and **selectors**. *(p.1, p.3, p.4)*
- Gives executable semantics for primitive operators over relational encodings: Domain, RestrictDomain, Invert, Compose, TransitiveClosure, Id, Subgraph, All, Copy, plus set operations Union, Difference, and Intersection. *(p.4, p.5)*
- Defines derived operators Range, RestrictRange, Traverse, Restrict, Extract, Delete, Match, and Merge, including implementation algorithms and points where human feedback is required. *(p.2, p.6-p.9)*
- Demonstrates change propagation between relational and XML schemas and a view-reuse scenario for SQL views. *(p.2, p.11, p.12)*
- Reports prototype scale: under 24K lines overall, with generic model-management functionality under 7K lines and scripts measured in hundreds of lines. *(p.10)*

## Study Design

## Methodology
The paper is a systems/design paper. It defines a weak but generic graph representation for metadata, gives relational semantics for operators, implements those operators in an interpreter-based prototype, and demonstrates them with schema-change propagation and SQL-view reuse scenarios. The authors deliberately keep many structures syntactic so operators can be shared across relational schemas, XML schemas, SQL views, RDF-like graph encodings, and preliminary UML support. *(p.3-p.10)*

## Key Equations / Statistical Models

$$
M(S:\mathrm{OID}, P:\mathrm{OID}, O:\mathrm{OID}\cup\mathrm{Literal}, N:\mathrm{integer})
$$

Where: a model is represented as a relation of directed labeled graph edges; `S` is the source node, `P` is the edge label, `O` is the target node or literal, and optional `N` orders sibling edges. *(p.3)*

$$
H(L:\mathrm{OID}, R:\mathrm{OID})
$$

Where: a morphism is a binary relation between two sets of model object identifiers; implementations may add attributes such as similarity scores. *(p.4, p.7)*

$$
S(V:\mathrm{OID})
$$

Where: a selector is a set of node identifiers, represented as a unary relation. *(p.4)*

$$
\mathrm{Compose}(map_1,map_2) := \{(l,r)\mid \exists m.\ (l,m)\in map_1 \land (m,r)\in map_2\}
$$

Where: composition is the relational join of morphisms, yielding a morphism from the left side of `map1` to the right side of `map2`. *(p.5)*

$$
\mathrm{TransitiveClosure}(map) := \mu TC.\ map \cup \{(l,r)\mid \exists m.\ (l,m)\in TC \land (m,r)\in map\}
$$

Where: the closure operator is expressed using recursive SQL in the paper. *(p.5)*

## Parameters

| Name | Symbol | Units | Default | Range | Page | Notes |
|------|--------|-------|---------|-------|------|-------|
| Model edge tuple attributes | `S,P,O,N` | fields | four fields | 3 required, 1 optional | 3 | Directed labeled graph encoded as relation. |
| Morphism arity | `L,R` | fields | two fields | optional extra attributes | 4,7 | Binary relation; Match adds similarity score `Sim`. |
| Selector arity | `V` | fields | one field | one or more model origins | 4 | Node set used to scope operators safely. |
| Rondo total code size | - | lines | below 24000 | - | 10 | Prototype total. |
| Generic model-management code | - | lines | below 7000 | - | 10 | Includes interpreter, primitive operators, SFjoin, GraphMerge, GUI operators. |
| Interpreter code | - | lines | 2050 | - | 10 | Figure 10 code breakdown. |
| Primitive operator code | - | lines | 660 | - | 10 | Figure 10 code breakdown. |
| SFjoin code | - | lines | 1760 | - | 10 | Similarity flooding join operator. |
| GraphMerge code | - | lines | 700 | - | 10 | Generic merge implementation. |
| GUI operator code | - | lines | 1400 | - | 10 | Generic editing GUIs. |
| Converter code | - | lines | 600 | - | 10 | Non-generic converter category in Figure 10. |
| XML schema code | - | lines | 1280 | - | 10 | Non-generic XML-schema support. |
| SQL DDL code | - | lines | 6800 | - | 10 | Non-generic SQL DDL support. |
| SQL views code | - | lines | 11820 | - | 10 | Largest non-generic category. |
| XSD2SQL converter | - | lines | 260 | - | 10 | Specific converter. |
| SQL2XSD converter | - | lines | 250 | - | 10 | Specific converter. |
| View2Morphism converter | - | lines | 90 | - | 10 | Specific converter. |
| Morphism2View converter | - | lines | 200 | - | 10 | Specific converter. |

## Effect Sizes / Key Quantitative Results

| Outcome | Measure | Value | CI | p | Population/Context | Page |
|---------|---------|-------|----|---|--------------------|------|
| Prototype footprint | LOC | below 24000 | - | - | Whole Rondo prototype | 10 |
| Reusable generic layer | LOC | below 7000 | - | - | Generic model-management code | 10 |
| Script complexity | qualitative LOC | hundreds of lines | - | - | Scenarios in paper, seconds on 600 MHz laptop with 256 MB memory | 10 |

## Methods & Implementation Details
- **Motivating change-propagation script:** `Match(s1,s2)` detects changes; deleted elements are obtained through `All(s1)-Domain(s1_s2)`, traversed through old mapping `s1_d1`, deleted from `d1`, new source elements are traversed through `s2_c`, extracted, then merged back with the retained target schema. The final mapping `s2_d2` is assembled by composing and inverting the intermediate morphisms. *(p.2, p.3)*
- **Models:** Rondo uses directed labeled graphs and keeps the representation deliberately weak; meta-models determine well-formedness, but many generic operators can run without understanding the semantics of the target language. *(p.3, p.4)*
- **Morphism semantics:** morphisms are weaker than SQL views or executable transformations because they contain no transformation semantics, but this weakness makes them invertible, composable, and easy to implement. *(p.4)*
- **Selectors:** selectors let operators work on safe subsets of models instead of arbitrary graph subsets that might be ill-formed. *(p.4)*
- **Primitive operators:** Domain, RestrictDomain, Invert, Compose, TransitiveClosure, Id, and Subgraph are expressed in SQL-like relational algebra; All and Copy are also primitive, with Copy preserving old-to-new OID correspondence. *(p.5)*
- **Extract:** Extract must return a well-formed model that contains selected nodes, is no more expressive than the input, captures all information needed by the selected nodes, and is minimal subject to those conditions. It may need closure over implicit information and cover extraction to preserve constraints. *(p.6)*
- **Delete:** Delete is defined as extraction of `All(m)-s`; nodes not representing model elements can affect deletion only indirectly through their impact on selected model elements. *(p.6)*
- **Match:** implemented with Similarity Flooding (`SFjoin`), then restricted and filtered to best matches. It is heuristic, returns candidate morphisms, and expects human review or iterative adjustment. *(p.7, p.8)*
- **Merge:** combines models using a morphism plus GraphMerge; merge handles node renaming, graph union, conflict resolution, and returns morphisms from inputs to output. Structural conflicts are not fully resolved automatically. *(p.8, p.9)*
- **Prototype:** Rondo has an interpreter, native operators, scripts, SQL table/file storage, graph APIs, converters, GUI editing operators, and debugging support for traces of complex scripts. *(p.9, p.10)*
- **Adding a modeling language:** requires import/export operators that round-trip losslessly and callbacks for All, Extract, and GraphMerge. *(p.10)*

## Figures of Interest
- **Fig. 1 (p.2):** Change propagation from a relational schema to an XML schema after deletes/additions/renames.
- **Fig. 2 (p.2):** Schematic solution for change propagation using source versions, target versions, converted schema, and morphisms.
- **Fig. 3 (p.4):** Relational table `PRODUCTS` represented as a graph and as 4-tuples.
- **Fig. 4 (p.4):** Morphism between relational and XML schema elements.
- **Fig. 7 (p.7):** Examples showing why extraction and deletion must preserve constraints and support nodes.
- **Fig. 8 (p.8):** GraphMerge conflict-resolution example with edge priority tags.
- **Fig. 9 (p.10):** Rondo architecture: interpreter over native operators/scripts backed by SQL tables and files.
- **Fig. 10 (p.10):** Prototype line-of-code breakdown.
- **Fig. 12 (p.12):** View-reuse scenario merging SQL views.

## Results Summary
Rondo implements all generic operators suggested in earlier model-management literature and adds selectors plus several new generic operators. The paper demonstrates that a weak graph representation can support a broad class of metadata tasks, but semantic gaps remain: subtle merge conflicts, semantic constraints, and structural transformations still need either user feedback or non-generic converter/operator support. *(p.8-p.11)*

## Limitations
- Match is inherently heuristic because schemas do not contain all information needed for fully automatic matching; human review is expected. *(p.6, p.7)*
- Merge does not invent new model elements or new structural relationships; it cannot by itself resolve structural conflicts such as merging two flat XML elements into a new complex element. *(p.9)*
- The implementation does not always guarantee that Merge output is at least as expressive as each input and minimal; a more restrictive Merge is future work. *(p.9)*
- Operators are mostly syntactic; assessing correctness ultimately requires semantic accounts of what transformations do to instances. *(p.11)*
- Adding a new modeling language requires non-generic import/export and callbacks for language-specific operations. *(p.10)*

## Arguments Against Prior Work
- Prior model-management papers reified mappings as models, but simple morphisms can provide substantial leverage; reified mappings add complexity to scripts and operator implementations. *(p.10)*
- Existing schema matching requires manual post-processing; Rondo reduces but does not eliminate this through structural Similarity Flooding and editable candidate morphisms. *(p.7, p.10)*
- Schema translation across modeling languages has often been implemented with custom converters; Rondo notes this is less general than a generic model-generation operator. *(p.11)*

## Design Rationale
- Use weak graph models because the same representation can encode relational schemas, XML schemas, SQL views, RDF-like structures, and UML preliminarily. *(p.3, p.9, p.10)*
- Use morphisms rather than executable mappings when generic invert/compose/manipulate behavior matters more than direct instance transformation semantics. *(p.4)*
- Use selectors to make subset operations safer and avoid malformed arbitrary graph subsets. *(p.4, p.5)*
- Preserve human feedback in Match and Merge because the paper treats semantic heterogeneity and conflict resolution as not fully automatable. *(p.2, p.7-p.9)*
- Implement operators as SQL/relational graph operations to reuse standard query processing and keep scripts compact. *(p.5, p.9)*

## Testable Properties
- A model representation must be able to encode metadata as directed labeled graph tuples with stable OIDs and optional sibling order. *(p.3)*
- Morphisms must support invert and compose generically; this is part of their practical advantage over more semantic executable mappings. *(p.4, p.5)*
- Extract must preserve enough support structure that selected model elements remain well-formed. *(p.6, p.7)*
- Delete must not silently leave a model inconsistent when constraints depend on deleted nodes. *(p.6, p.7)*
- Match output should be editable before downstream propagation because the paper treats full automation as unrealistic. *(p.2, p.7)*
- Merge output must return input-to-output morphisms so downstream scripts can continue composing relationships. *(p.8, p.9)*

## Relevance to Project
For quire, Rondo backs an architecture in which schema families and contract versions are first-class models; inter-version relations are morphisms; slices of a schema are selectors; and change propagation is expressed through composable operators rather than bespoke migration code. It specifically supports quire claims about model-at-a-time schema evolution, explicit mappings between related artifacts, human-in-the-loop conflict resolution, and graph-backed internal representations for schemas/contracts.

## Collection Cross-References

### Already in Collection
- (none yet; related Bernstein paper is being processed in this Worker A slice)

### New Leads (Not Yet in Collection)
- Bernstein (2003), "Applying Model Management to Classical Meta Data Problems" - overview and operator repertoire that Rondo implements and extends. *(p.1, p.10)*
- Melnik, Rahm, and Bernstein (2003), "Rondo: A Programming Platform for Generic Model Management (Extended Version)" - extended technical report version cited for more detail. *(p.12)*
- Rahm and Bernstein (2001), "A Survey of Approaches to Automatic Schema Matching" - schema matching background for the Match operator. *(p.12)*
- Melnik, Garcia-Molina, and Rahm (2002), "Similarity Flooding" - graph matching algorithm used by Match. *(p.7, p.12)*

### Conceptual Links (not citation-based)
- (pending after the other Worker A papers are read)

## Open Questions
- [ ] What contract-family representation gives enough structure for generic operators without losing semantics needed for compatibility checks? *(p.3-p.5, p.11)*
- [ ] Which quire merge conflicts can be solved syntactically and which require semantic rules or user decisions? *(p.8-p.11)*

## Related Work Worth Reading
- Bernstein (2003), "Applying Model Management to Classical Meta Data Problems" - defines the broader model-management operator program.
- Rahm and Bernstein (2001), "A Survey of Approaches to Automatic Schema Matching" - background for Match.
- Melnik, Garcia-Molina, and Rahm (2002), "Similarity Flooding" - graph matching algorithm.
- Pottinger and Bernstein (2003), "Merging Models Based on Given Correspondences" - merge influence.
