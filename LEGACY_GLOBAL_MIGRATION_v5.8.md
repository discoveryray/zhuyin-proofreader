# Phase 5: auditable legacy candidate migration

`migrate_legacy_global_candidates.py` validates one explicitly selected project.
The default operation is a dry run. It prints a technical JSON report to stdout;
validation errors produce a `BLOCKED` report on stderr and exit code 1.

```powershell
.\.venv\Scripts\python.exe migrate_legacy_global_candidates.py --project-output C:\Books\project
.\.venv\Scripts\python.exe migrate_legacy_global_candidates.py --project-output C:\Books\project --pdf-root C:\Books\source --apply
```

`--pdf-root` may be repeated. Only exact filename direct children of the selected
project and explicitly supplied PDF roots, or a manifest's stored PDF path, are
considered. Every resolved PDF must match its sealed SHA-256. There is no recursive
discovery. Artifact relocation uses the project's existing `01_實際注音` and
`02_候選報告` directories; it never reads another project's workbook as a substitute.

Tests and development must pass `--global-library-root` with an isolated temporary
directory. Without that option the operator CLI uses the existing production
Global Library location. Only `--apply` can initialize an absent library or write
imports. The Global root must be outside the selected source project.

## Validation and source boundaries

The service `legacy_global_migration.migrate_project()` always rebuilds a fresh
plan using `build_migration_plan()`. Both read the source project without repair,
initialization, refresh, staging, or ledger mutation. The manifest's seal, schema
contracts, PDF roster, raw artifact hashes, actual workbook metadata and occurrence
roster are checked. Candidate workbooks are read as bytes for hash verification;
their readings are not parsed. Input hashes are rechecked after validation.

The actual-only occurrence index joins sealed records to current actual workbook
IDs, PDF identity, page, character and coordinates. Exact identities are re-parsed
from the current embedded font attached to that occurrence. TTF requires the Phase 1
visible simple-glyf contract; CFF requires the complete recording SHA and style.
Unsupported TTF structure produces `NON_GLOBAL_ELIGIBLE` diagnostics without Global
rows. Missing, mismatched or unprovable sources block the entire selected project.

Required CSVs are the TTF learning file, CFF learning file, and conflict registry
under `_專案證據/actual`. Headers, canonical readings, identities, levels and source
counts are validated. Duplicate canonical rows fail. Optional override/provenance
files, when present, are checked for headers and reading syntax and remain audit
only. They are never created or migrated by this service.

## Storage and compatibility

SQLite schema `1.0` / `user_version=1` is unchanged. Migration import contract
`1.0` is independent of the SQLite schema. Its derived row key hashes canonical
`glyph_id`, `reading`, and `old_verification_level`. The import ID also binds the
sealed session ID, raw manifest SHA and raw source CSV SHA. Row numbers, notes,
timestamps and filesystem paths are not additional identity fields. Copying an
unchanged project preserves import identity; changing source bytes creates a new
auditable import.

Strict reads accept the exact frozen Phase 2 draft ID algorithm as a private
legacy-v0 adapter. Production writes emit only current contract IDs and never
rewrite old IDs.

Current-contract rows must retain a matching committed receipt and import
provenance. Missing or inconsistent links fail strict loading. This requirement
does not retroactively invent receipts for the exact legacy-v0 draft format.

Legacy records live in `migration_candidate`, not `source_evidence`:

| Status | Meaning |
|---|---|
| `CANDIDATE` | Identity revalidated and at least one original sample maps to a current occurrence |
| `INSUFFICIENT` | Identity revalidated but no original sample can be mapped |
| `CONFLICT` | Explicit project conflict or participation in Global quarantine |

None contributes to quorum. An effective selected-project import has one SQLite
write transaction, checks all replay receipts before mutation, and increments
generation at most once. Same ID/payload replay returns existing receipts without
new provenance. Any write failure rolls back the entire import transaction. An
explicit apply may leave a valid empty initialized library if a later import fails.

Migration and direct readings share only the conflict safety gate. Contradictions
in either arrival order quarantine the identity, retain union readings, and revoke
active approvals. Matching legacy audit growth preserves verified truth, revision
and the logical subset digest. Quorum remains exclusively Phase 4 direct evidence.

## Fresh confirmation

The report's deterministic `reconfirmation_targets` lists only mapped historical
sample IDs and current locations. It does not supply a prefilled actual decision,
stage corrections or generate direct evidence. Operators must visually confirm
again through the existing Phase 4 workflow; only those new events can count.

The report's `intents` preserve the original canonical import payload/status;
stored migration statuses may subsequently become `CONFLICT`. Receipts identify
the committed transaction and are stable across retries.
