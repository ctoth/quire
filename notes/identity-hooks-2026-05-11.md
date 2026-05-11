# Generic Identity Hooks

Date: 2026-05-11

## Decision

Existing Quire hooks are sufficient for the first Propstore semantic-artifact
cutover. No Quire code extension is required before Propstore moves canonical
stance storage from aggregate bucket files to one semantic artifact per family
document.

## Current Generic Surface

- `ArtifactFamily.document_payload` lets a package provide a typed document to
  payload projection without Quire knowing the document semantics.
- `ArtifactFamily.normalize_for_write` and `validate_for_write` let a package
  stamp, rebuild, or reject typed documents before encoding.
- `ArtifactFamily.coerce_payload`, `decode_bytes`, `encode_document`, and
  `render_document` let a package override typed document IO at the family
  boundary.
- `FamilyIdentityPolicy` records artifact id, version id, canonical payload,
  normalization, logical-id fields, version-excluded fields, and source-local
  fields in the contract surface.
- `DocumentFamilyStore.payload`, `coerce`, `prepare`, and `save` keep the
  typed-document boundary in Quire while leaving semantic identity policy in the
  owning package.

## Boundary

Quire should remain schema-blind. It can provide canonical document IO, family
placement, typed lifecycle hooks, and contract metadata, but it should not learn
Propstore artifact grammars, stance target rules, source promotion semantics,
sidecar behavior, or claim/justification verification.

Propstore should continue to own semantic identity functions, source-local field
exclusion, artifact-code verification, and cross-family references.
