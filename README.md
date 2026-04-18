Quire is a typed, schema-aware document store built on git objects, refs, and
notes. It provides the generic storage substrate that semantic applications can
use without depending on a materialized working tree.

Current package surface:

- object-store-first `GitStore`
- validated refs and notes refs
- strict `msgspec` document decoding
- typed loaded-document envelopes
- checked contract manifests for versioned persisted ABIs
