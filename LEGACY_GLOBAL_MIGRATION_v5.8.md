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
Global Library location. Only a nonempty `--apply` can initialize an absent library
or write imports. The Global root must be outside the selected source project.
An empty batch (no donors or all `NON_GLOBAL_ELIGIBLE`) still strictly validates
an existing Global DB through a read-only snapshot. Corruption, unknown schema or
any validation failure produces `BLOCKED`; absence remains absent. A valid no-op
adds no generation, receipt or provenance and never repairs/replaces the DB.

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

A quarantined learning row is omitted from delivery only after every retained
`source_examples` ID has been checked against the current exact identity and its
retained reading is present in the same identity's conflict registry. A current
sample that lost its exact identity, or a retained reading omitted from that
registry, blocks the entire project before the first Global mutation (including
initialization). Historical sample IDs no longer in the current roster may remain
unmapped. Valid learning samples augment only the matching conflict import's
local reconfirmation mapping; the conflict CSV remains the reading donor and the
source hash bound by the import. No project bytes or donor evidence are repaired.

## Storage and compatibility

SQLite schema `1.0` / `user_version=1` is unchanged. Migration import contract
`1.0` is independent of the SQLite schema. Its derived row key hashes canonical
`glyph_id`, `reading`, and `old_verification_level`. The import ID also binds the
sealed session ID, raw manifest SHA and raw source CSV SHA. Row numbers, notes,
timestamps and filesystem paths are not additional identity fields. Copying an
unchanged project preserves import identity; changing source bytes creates a new
auditable import.

Strict reads accept the exact frozen Phase 2 draft ID algorithm as a private
`legacy-v0` adapter, selected by `_stored_migration_contract()`. Only the exact old
algorithm or current contract ID is accepted; unknown IDs fail closed. Production
imports emit only current contract IDs. Later direct/migration conflict delivery
preserves every old row column, including `status` and `import_id`; it updates the
durable glyph quarantine/open conflict and current-contract migration statuses.

Current-contract rows must retain a matching committed receipt and import
provenance. Missing or inconsistent links fail strict loading. This requirement
does not retroactively invent receipts for the exact legacy-v0 draft format.

### Frozen v0 state compatibility boundary

The reference is the actual `global_exact_glyph_library.py` and architecture audit
at baseline `d2d558808bd2902857fad30f824d8bdc352beb73`. Audit section 16 requires
disagreeing candidates and explicit project conflicts to quarantine immediately;
section 7.2 requires an open conflict and no active reusable reading. Phase 2's
validator checked migration IDs/fields but omitted the relationship between
migration readings and glyph/conflict state. Acceptance by that validator alone
does not establish conformance to the frozen safety contract.

| Historical state (otherwise valid schema/fields/IDs) | Current strict read |
|---|---|
| Single or multiple agreeing v0 candidates; no contradictory direct/trusted evidence | Valid; migration contributes zero quorum |
| v0 `INSUFFICIENT` candidate with no contradiction | Valid; no sample, receipt or direct evidence invented |
| Contradictory v0 readings with durable `QUARANTINED_CONFLICT` and one open conflict covering every reading | Valid; historical migration statuses may remain `CANDIDATE` unchanged |
| Explicit v0 conflict with the same complete durable quarantine | Valid; never reusable |
| Contradictory candidates or explicit conflict while glyph remains `CANDIDATE` | Blocked, even though baseline validator returned `VALID` |
| Open conflict omits a retained migration reading, or verified truth contradicts a v0 reading | Blocked; no partial read or automatic DB repair |
| Unknown or forged import ID | Blocked by the finite ID adapter |

All accepted states still satisfy strict source counts, approval, schema and
conflict validation. There is no read-time synthetic quarantine and no weakening
of the common conflict gate. Admitting an unquarantined contradictory historical
DB would require a separate audited repair/contract decision; this migration does
not authorize that state. Safe historical states require no such decision.

`tests/test_global_legacy_migration_review_v580.py` executes the pinned baseline
implementation's actual schema creation, ID calculation, transaction validator
and strict reader to produce and cross-read the fixtures above. Git history at
the pinned commit is required; it is not replaced by current private helpers or
silently skipped. Tests also check current-only production imports, preservation
of v0 rows after both conflict delivery paths, and matching verified subset digest.

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

## Independent-review corrections: validation on 2026-09-09

Correction base: reviewed commit `7b59a7aa1a0e163f99f2bac5bca083bb5ad31bc6`,
whose sole parent is baseline `d2d558808bd2902857fad30f824d8bdc352beb73`.
The branch remains `feat/v58-legacy-candidate-migration`; the original commit and
baseline are preserved. Review must cover the cumulative baseline-to-new-HEAD
diff, including the original Phase 5 implementation.

Environment: Windows, Python 3.13.5, `PYTHONUTF8=1`, `PYTHONIOENCODING=utf-8`.
Test processes use isolated TEMP/LOCALAPPDATA roots, never the live Global DB.

| Executed validation | Result |
|---|---|
| Phase 5 unittest discovery, `test_global_legacy_migration*_v580.py` | 54 tests passed, including 11 new review regression tests |
| Phase 1 identity, Phase 2 repository, Phase 3 reuse, Phase 4 promotion/recovery, cross-version compatibility pytest | 180 passed, 74 subtests passed |
| Full `python -m unittest discover -s tests -v`, desktop execution | 450 tests passed; no failures, errors or skips |
| Full `python -m pytest -q tests -o cache_dir=<isolated cache>`, all 39 tracked test files | 517 passed, 151 subtests passed; no skips; 5 PyMuPDF/SWIG deprecation warnings |
| Windows spawn multiprocessing/locking in the above suites | Passed: same/different migration readings, direct promotion, same-intent replay, CAS, bounded locking and crash rollback |
| `validate_asset_manifest()` | All 20 assets passed; no errors/warnings; dictionary has 45,130 rows and SHA `a9a4fd7259180113bfae2e94110eae87ac4dcf0bfcc91a6437c3ad4773ab7865` |
| `python -m compileall -q .` with isolated bytecode cache | Exit 0 |
| Frozen Phase 2 `75d41abd9d2aa6de4e5b9b0ac0eb5dc0b4610700` to specified baseline ID algorithm AST comparison | Identical |
| Baseline-to-correction working/index diff whitespace checks | Passed; committed range is rechecked after commit |

B1 tests cover a retained `ㄇ` missing from the `ㄅ|ㄆ` registry, a current sample
that lost its exact identity, and valid TTF/CFF quarantine with usable mapping.
Failures preserve all project bytes and existing Global evidence, with no partial
import or absent-store initialization. B2 uses the actual baseline algorithm and
validator for single, agreeing, insufficient, contradictory and explicit-conflict
rows, invalid IDs, immutable v0 rows and current-only imports. B3 exercises both
no-donor and all-ineligible plans against absent, valid, corrupt, unknown-schema
and invalid-row stores in dry-run and apply modes; valid no-ops preserve all tables.

Initial environment failures were investigated before reruns: two pre-existing
recovery tests read UTF-8 JSON using Windows CP950 until UTF-8 mode was enabled;
sandbox Tcl loading skipped two GUI classes although the installed Tcl 8.6.15
worked outside the sandbox; and root pytest discovery encountered an inaccessible
sandbox-generated temporary directory. The final desktop suites executed every
tracked test from `tests/` with accessible temporary/cache paths and no GUI skips.
SQLite read-only opens may create/remove transient SHM or empty WAL files; byte
assertions preserve the main DB and every nonempty WAL byte, and no-op tests also
compare every logical table, including generation, receipts and provenance.

Not executed: Python 3.12/Linux matrix runs, real-user project migration, live
Global DB mutation or real-textbook acceptance. This task prohibits real project
and live DB writes; the migration fixtures use embedded-font synthetic PDFs.
Runtime assets, manifest, fingerprint contracts, semantics epochs, decoder
precedence, SQLite tables/columns and occurrence/review identities are unchanged.
No unresolved implementation blocker or new contract decision remains within this
scope. Independent review of the cumulative diff is still required; these results
are implementation validation, not merge approval.
