# Abstract

## Original Text (Verbatim)

Model management aims at reducing the amount of programming needed for the development of metadata-intensive applications. We present a first complete prototype of a generic model-management system, in which high-level operators are used to manipulate models and mappings between models. We define the key conceptual structures: models, morphisms, and selectors, and describe their use and implementation. We specify the semantics of the known model-management operators applied to these structures, suggest new ones, and develop new algorithms for implementing the individual operators. We examine the solutions for two model-management tasks that involve manipulations of relational schemas, XML schemas, and SQL views.

---

## Our Interpretation

The paper turns model management from an operator list into a runnable platform. Its main value for quire is the representation discipline: contracts and schema versions can be modeled as graph artifacts connected by morphisms, with evolution expressed as operator scripts plus human review where semantic matching cannot be automated.
