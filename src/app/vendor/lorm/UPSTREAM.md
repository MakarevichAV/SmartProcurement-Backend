# Vendored LORM reference implementation

These files are copied **unmodified** from the official LORM reference implementation and
are pinned to an exact commit for reproducibility (tasks.md T019, Constitution §3,
research.md §6a).

| | |
|---|---|
| Upstream | https://github.com/Argyronix/lorm |
| Pinned commit | `346a8a7d04b14091bab8f8c6513c80adaed83f61` |
| Commit date | 2026-08-10 |
| Retrieved | 2026-09-07 |
| License | Apache-2.0 (see `LICENSE`) |

## Files

| Vendored path | Upstream path | Purpose |
|---|---|---|
| `lorm-policy.schema.json` | `schema/lorm-policy.schema.json` | Canonical shape of an L5 policy document. |
| `validate_policy.py` | `skills/lorm/scripts/validate_policy.py` | Schema + semantic validator (SPEC 8-1 author≠approver, expiry, demotion refs). |

`SPEC.md` is **not** vendored as a file copy (large reference prose). It is pinned by URL:
<https://github.com/Argyronix/lorm/blob/346a8a7d04b14091bab8f8c6513c80adaed83f61/SPEC.md>.
The SPEC clauses Smart Procurement implements (§6.2/§6.3 promotion/demotion, §8 policy block,
§10.1 verification, §10.3 audit fields, invariants I-1…I-8) are captured as service logic and
test oracles, and summarised in `docs/lorm-integration.md` (tasks.md T160).

## Updating

Bump the commit above, re-copy the two files unchanged, run `pytest`, and update
`docs/lorm-integration.md`. Do not edit vendored files in place — adaptations live in
`app/policies/schema_validator.py` and `app/lorm/`.
