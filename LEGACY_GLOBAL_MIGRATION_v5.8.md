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

### Bounded historical-candidate compatibility (adopted v1.1 section 5.6)

The controlling exception is section 5.6 of the adopted v1.1 specification,
available as [V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md](https://github.com/discoveryray/zhuyin-proofreader/blob/f8bcb7f6a86e645b598715f0fd0ba198c2d0ccc1/docs/V58_MASTER_DEVELOPMENT_REVIEW_PLAN_v1.1.md).
It takes precedence over the baseline audit's general candidate-quarantine rule.
The actual library at baseline `d2d558808bd2902857fad30f824d8bdc352beb73` remains
the frozen schema/ID/validator/reader reference. Baseline `VALID` alone cannot
exempt a store from evidence, reference, count, identity or approval validation.

The exception is finite: exact `legacy-v0` import ID, old verification level
`USER_VERIFIED_SINGLE` or `VERIFIED_EXACT_GLYPH`, and historical row status
`CANDIDATE` or `INSUFFICIENT`. All fields and all other strict validations still
apply. Explicit legacy conflicts and current-contract imports retain their
durable conflict requirements. No legacy row contributes to independent quorum.

| Historical state (otherwise valid schema/fields/IDs) | Current strict read |
|---|---|
| Single or multiple agreeing v0 candidates; no contradictory direct/trusted evidence | Valid; migration contributes zero quorum |
| v0 `INSUFFICIENT` candidate with no contradiction | Valid; no sample, receipt or direct evidence invented |
| Contradictory v0 readings with durable `QUARANTINED_CONFLICT` and one open conflict covering every reading | Valid; historical migration statuses may remain `CANDIDATE` unchanged |
| Explicit v0 conflict with the same complete durable quarantine | Valid; never reusable |
| Ordinary v0 candidates disagree while stored glyph is `CANDIDATE` or `PROMOTION_READY` | Strict-readable; effective conflict suppresses exact reuse immediately |
| Ordinary v0 reading contradicts otherwise valid direct/active reading, including stored `VERIFIED_GLOBAL` | Strict-readable; suppress this identity's trusted reuse immediately |
| Explicit legacy conflict without durable quarantine, or contradictions among non-exempt current/direct/active readings without durable quarantine | Blocked |
| Durable open conflict omits a retained migration reading | Blocked; no partial read or automatic DB repair |
| Contradictory retained direct evidence, invalid/stale approval, invalid counts/references/schema | Blocked; the ordinary v0 exception cannot hide these errors |
| Unknown or forged import ID | Blocked by the finite ID adapter |

`GlobalExactGlyphSnapshot.read_side_conflicts` contains immutable diagnostics with
the stored state, all applicable union readings and exact historical import IDs.
Affected records are excluded from `trusted_identities`. They are not reported as
durably quarantined in `quarantined_identities`. Pure read, snapshot, dry-run and
empty apply never alter old rows/IDs/status, add receipts, persist quarantine or
revoke an approval. Dry-run/empty-apply reports expose `global_read_side_conflicts`.

The existing logical dependency contract represents both kinds of effective
conflict as `QUARANTINED_CONFLICT` with the union readings and no active reading;
this is an effective reuse state, not a claim about persisted storage. The
existing exact union conflict gate then suppresses project/Global/static exact
donors. Independently gated decoders and occurrence-specific direct overrides
keep their original precedence. Only relevant per-PDF dependencies change;
diagnostics, IDs, generation, whole DB bytes and audit growth are not cache keys.

New direct delivery and new migration continue through the existing whole-batch
transaction and include every retained v0 reading. Even an incoming reading
matching one historical row must preserve the other row as counterevidence.
They atomically persist quarantine, clear active reading, revoke applicable
approval and retain all readings, while every v0 row column stays unchanged.

Explicit approval validates the request policy, revision, exact direct-evidence
roster and quorum first. A valid request against a compatible historical conflict
in `PROMOTION_READY` or `VERIFIED_GLOBAL` commits the same conflict transaction
and returns `approval_granted=false`, empty `approval_id`, state
`QUARANTINED_CONFLICT`, and a receipt. It does not raise an approval rejection
inside the transaction and inadvertently roll back quarantine. Invalid/stale
requests remain rejected without mutation. The `approval_conflict_` receipt binds
the new request, with linked `CONFLICT_OPENED` provenance; these are not retroactive
v0 import receipts. Exact retry returns the original receipt without another
generation bump, revocation or event. A transaction failure rolls back everything.

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
stored current-contract migration statuses may subsequently become `CONFLICT`.
Historical v0 statuses remain unchanged. Receipts identify the committed
transaction and are stable across retries.

## Prior correction validation (superseded for B2)

The following results belong to reviewed HEAD
`3ac6d72bd581d1e57b215d1d2076566cf094a3b1`. Independent review confirmed B1/B3 but
blocked B2 because ordinary historical candidates were incorrectly rejected.
They are historical execution evidence, not validation of the correction below.

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
That prior implementation did not satisfy the adopted section 5.6 exception.
Independent review of the latest cumulative diff is still required.

## B2 corrective implementation validation

Correction parent: `3ac6d72bd581d1e57b215d1d2076566cf094a3b1`, followed by
`7b59a7aa1a0e163f99f2bac5bca083bb5ad31bc6` and Phase baseline
`d2d558808bd2902857fad30f824d8bdc352beb73`. Local and remote feature HEAD and
this parent chain were checked after fetch before implementation and again
before committing. No reviewed commit was amended or rebased. Review scope
remains the entire Phase baseline to latest feature HEAD.

The B2 fixtures execute the real pinned baseline schema creator, ID algorithm,
transaction validator and reader. They cover ordinary v0 `ㄅ`/`ㄆ` rows in
`CANDIDATE`, `PROMOTION_READY` and `VERIFIED_GLOBAL`, including a single contrary
v0 reading against valid direct quorum. Reads, dry-run and empty apply keep all
tables unchanged; a held baseline WAL read transaction verifies preservation of
the main DB and nonempty WAL bytes. Effective diagnostics remain separate from
durable quarantine. An unrelated verified identity still resolves and keeps its
baseline subset/hash, while only relevant dependencies become conflicted.

Fresh direct delivery, fresh migration and valid explicit approval each persist
the union conflict even when their reading matches one v0 row. Tests assert
cleared active reading, applicable revocation, no new approval or legacy quorum,
unchanged v0 columns, linked audit records, stable receipt replay and one
transaction generation increment. Injected failure after conflict/revocation and
receipt creation rolls everything back. Windows spawn races cover direct versus
migration and duplicate approval requests. Tests also retain matching verified
digest, style-scoped CFF dependencies, independent decoder fallback and
occurrence-local direct override precedence.

True unsafe stores remain negative tests: explicit legacy conflict without
quarantine, incomplete conflict readings, contradictory direct evidence,
invalid approval/counts/schema/IDs, and missing current import or approval-conflict
receipts/provenance. B1 TTF/CFF retained-reading/reconfirmation and B3 empty-apply
matrices still run unchanged.

Local validation environment: Windows, Python 3.13.5, UTF-8 mode. Complete suites
run outside the sandbox so installed Tcl/Tk GUI tests execute, with isolated
TEMP and LOCALAPPDATA under `C:\Work\phase5-b2-20260909-desktop`. The full pytest
target is `tests/`, containing all 39 tracked test files; its cache is outside the
sandbox-generated temporary directories. No new skip or replacement dictionary
was introduced.

Executed commands and results:

```text
python -m unittest discover -s tests -p "test_global_legacy_migration*_v580.py" -v
  66 tests passed (27.501s).
python -m pytest -q tests/test_exact_glyph_identity_v580.py tests/test_global_exact_glyph_repository_v580.py tests/test_global_exact_glyph_read_reuse_v580.py tests/test_global_promotion_v580.py tests/test_actual_post_commit_recovery_v580.py tests/test_cross_version_compat_v560.py -o cache_dir=tmp/phase5-b2/cache
  180 passed, 74 subtests passed (29.16s); 5 existing SWIG warnings.
python -m unittest discover -s tests -v
  462 tests passed (239.384s), no failures, errors or skips.
python -m pytest -q tests -o cache_dir=C:/Work/phase5-b2-20260909-desktop/pytest-cache
  529 passed, 181 subtests passed (235.83s); no skips; 5 existing SWIG warnings.
python -c "from pathlib import Path; import runtime_source_validation as r; import json; v=r.validate_asset_manifest(Path.cwd()); print(json.dumps(v,ensure_ascii=False,indent=2)); raise SystemExit(0 if v['ok'] else 1)"
  All 20 runtime assets valid, no errors/warnings; manifest and assets unchanged.
python -m compileall -q .
  Exit 0, with PYTHONPYCACHEPREFIX in the isolated test cache.
git diff --check d2d558808bd2902857fad30f824d8bdc352beb73
  Passed before commit; baseline-to-committed-HEAD is rechecked for handoff.
```

The first expanded Phase 5 run found a fixture setup error: the WAL holder had
not started a read transaction, so SQLite removed the WAL before the byte check.
Starting that baseline read transaction made the intended nonempty WAL fixture
real; the final run above passed without weakening its assertions.

Local Python 3.12 is unavailable. The existing `ci.yml` workflow supplies Windows
3.12/3.13 through `workflow_dispatch`; final commit SHA and CI results are recorded
in the handoff after push. Live DB and real-user project mutations, real-textbook
acceptance and Linux are not executed. Synthetic embedded-font fixtures provide
the isolated migration/decoder verification for this bounded objective.

SQLite `1.0`/`user_version=1`, actual/expected separation, identities, quorum,
semantics epochs, runtime assets and fingerprint scope remain unchanged. No whole
DB hash or generation cache key is introduced. No PR, merge, tag, release,
main/develop mutation or Phase 6 work belongs to this correction. Implementation
completion is `READY FOR REVIEW`; independent review remains the merge gate.
