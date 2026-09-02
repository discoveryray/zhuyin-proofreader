# v5.8 Global Exact Glyph Library Architecture Audit

Audit date: 2026-09-01
Repository baseline: `facb0b0022a9c62e6d23fd8ee541c671cbe190cc` (`origin/develop`)
Scope: architecture audit and phased design only; this document does not change production behavior.

## 1. Executive summary

v5.7 already has a strong project-local manual-actual path: a visual decision is durably staged, a whole batch is revalidated and transactionally applied to five project-owned actual evidence files, conflicts quarantine the exact glyph instead of overwriting it, and one full-session refresh relies on per-PDF exact dependencies to reuse unaffected caches.

That design cannot be copied directly into a cross-project library. The current project promotion threshold is only two unique `occurrence_id` values. Two occurrences may come from the same PDF, the same embedded font, and the same review action; that is adequate for controlled project-local propagation but is not independent enough to authorize future books automatically.

The unique recommendation for v5.8 is:

1. Store the library outside Git and outside every project at `%LOCALAPPDATA%\DiscoveryRay\ZhuyinProofreader\GlobalExactGlyphLibrary\library.sqlite3`.
2. Keep one canonical identity registry: a TTF global identity exists only for a legally parsed simple `glyf` record and uses the SHA-256 of that complete raw record; CFF is `(style_group, complete resolved CFF glyph-recording SHA-256)`.
3. Admit only directly checked visual actual evidence. Expected evidence, dictionary data, semantic/context inference, normalized outlines, components, font names, and font indexes are ineligible.
4. Require two correlation-resistant independent sources—distinct project identity, PDF SHA-256, and embedded-font-program SHA-256—with the same reading, followed by an explicit promotion approval bound to that exact evidence quorum. Until then the row is a non-reusable candidate.
5. On any contradictory direct reading, retain the new occurrence override but atomically place the exact identity in durable global quarantine. Never use last-writer-wins.
6. Keep project evidence and global storage as separate transaction domains. Commit a promotion intent to a project-local transactional outbox with the project batch, then deliver it idempotently in one independent SQLite transaction.
7. Add a required per-PDF global exact-subset digest to the actual fingerprint contract. Do not fingerprint the whole database file or global generation counter.
8. Do not bump `ACTUAL_DECODER_SEMANTICS_EPOCH` merely for this evidence source. Do create an incompatible actual fingerprint schema/reuse-policy contract and a strict global-library schema.

The recommended implementation is split into six separately reviewable phases. Exact-identity hardening is completed before the global repository exists. No phase may introduce normalized-outline or component-level global learning.

## 2. Current v5.7 architecture

### 2.1 End-to-end flow

```text
ReviewApp.correct_actual()
  -> materialize current in-memory ledger for preview
  -> build_actual_group_for_entry() for the dialog only
  -> ActualReadingDialog: direct PDF-page visual reading, no expected lane
  -> stage_manual_actual_correction()
       -> strict manifest reload + integrity validation
       -> DB reload + current ledger materialization
       -> current entry lookup by review_id
       -> rebuild current group
       -> stage_manual_actual_group()
       -> manual_actual_staging.json only

User presses "套用 actual 修正（N）"
  -> apply_staged_manual_actual_corrections()
       -> strict manifest/artifact validation
       -> current DB + ledger
       -> load frozen staging
       -> rebuild every live group from current ledger members
       -> apply_staged_manual_actual_batch() exactly once
            -> whole-batch identity/snapshot/member/CAS validation
            -> snapshot five project-local authoritative files
            -> deterministic apply_verified_actual_group() per group
                 -> occurrence-specific override
                 -> project-local TTF/CFF learning update
                 -> VERIFIED_EXACT_GLYPH or conflict quarantine
                 -> append provenance
            -> one atomic staging acknowledgement
            -> byte-for-byte rollback of all five files on failure
       -> _clear_actual_dependent_events() once for union of affected IDs
       -> refresh_actual_project() once
            -> run_pipeline_pdfs(complete session PDF roster)
            -> per-PDF scoped fingerprint/reuse decision
            -> decode each invalidated PDF at most once
            -> CFF reads whole batch, writes only invalidated workbooks
            -> candidate reuse/rebuild, full manifest, ledger,
               reconciliation, regression and completion gate
       -> verify every directly checked occurrence actual == staged reading
```

Staging changes only the actionable GUI view. The authoritative ledger state remains non-terminal until batch apply and refresh complete.

### 2.2 Files and responsibilities

| File | Current role | Authoritative actual? | In `dynamic_actual_hashes()`? |
|---|---|---:|---:|
| `_專案證據/actual/manual_actual_staging.json` | Durable, restart-safe pending intent; exact-group upsert and CAS acknowledgement | No | No |
| `manual_actual_occurrence_overrides.csv` | PDF/page/character/stable-key/coordinate-scoped manual actual; applied after every automatic decoder | Yes, project-local | Yes, PDF-scoped |
| `user_verified_glyf_fingerprints.csv` | TTF raw-glyf exact learning records and `verification_level` | Yes, project-local | Yes, exact SHA subset |
| `user_verified_cff_glyph_fingerprints.csv` | CFF style + complete-glyph SHA exact learning records | Yes, project-local | Yes, exact `(style, SHA)` subset |
| `glyph_truth_conflicts.csv` | Durable exact-identity conflict/quarantine registry | Yes, project-local | Yes, exact dependency subset |
| `glyph_truth_provenance.csv` | Append-only-ish learning/override/conflict audit events | Yes for audit/rollback | No; it does not affect decode output |
| `校對工作階段.json` | Sealed whole-session manifest, artifact hashes, PDF roster and fingerprints | Session authority | Indirectly records fingerprints |
| `人工判定資料庫.json` | Expected/scope/difference review events; actual-dependent difference confirmations may be revoked | Review authority, not glyph truth | No |
| Actual workbook | Decoded rows plus `TTF字形SHA256`, `CFF樣式群組`, `CFF整字字形SHA256` and fingerprint metadata | Derived artifact | Supplies per-PDF dependencies |

`initialize_project_actual_evidence()` owns the `_專案證據/actual` boundary. It may seed missing files once from an installation for backward compatibility, but afterwards the project copy is authoritative.

### 2.3 Current learning and conflict semantics

`_update_learning_file()` merges unique `occurrence_id` values from `source_examples`:

- one source ID -> `USER_VERIFIED_SINGLE`;
- two or more source IDs -> `VERIFIED_EXACT_GLYPH`;
- a different canonical reading, a static TTF exact disagreement, or an already quarantined identity -> `GLYPH_TRUTH_CONFLICT`, with the learning row demoted to `QUARANTINED_CONFLICT`.

This count does not currently distinguish projects, PDFs, embedded font programs, direct checked members, or propagated peers. Therefore it must remain a project-local policy and must not become the global promotion policy.

`apply_verified_actual_group()` preserves occurrence truth independently from reusable learning:

- directly checked positions always receive occurrence overrides;
- when there is no conflict, a single-member group or two directly checked group members may propagate overrides to all current peers;
- on conflict, only direct/previously direct positions remain pinned, unverified propagated peers are reopened, and reusable exact truth is quarantined.

`load_user_verified_glyf()` and `load_user_verified_cff()` expose only `VERIFIED_EXACT_GLYPH` rows. Singles and quarantined rows are not reusable. `load_glyph_truth_quarantine()` suppresses reusable exact donors; for TTF this includes both built-in and user exact SHA donors. A static/user TTF mismatch is defensively suppressed even if an old conflict row is missing.

### 2.4 Current cache flow

`actual_workbook_dynamic_dependencies()` extracts:

- `ttf_glyph_sha256` from `TTF字形SHA256`;
- `cff_glyph_keys` from `CFF樣式群組` + `CFF整字字形SHA256`.

An older workbook without those columns returns `None`, which currently selects a conservative whole-project dynamic-evidence fallback. With a dependency roster, `dynamic_actual_hashes()` first validates all project evidence, then hashes only:

- occurrence overrides applicable to that PDF;
- TTF exact rows for SHA values present in that PDF;
- CFF exact rows for `(style_group, SHA)` values present in that PDF;
- conflicts for those same exact identities.

`compute_actual_asset_fingerprint()` places this mapping in the required actual compatibility contract. `run_pipeline_pdfs()` freezes the previous manifest before writes, computes every PDF fingerprint, calls `output_is_reusable()`, decodes only invalidated PDFs once, and lets unchanged candidates reuse the independent expected fingerprint. The full PDF roster is always retained for whole-book and CFF cross-file contracts.

## 3. Safety invariants

The global design must preserve all of the following:

1. Global data is actual evidence only. It cannot read expected sets, dictionaries, candidate equality, semantic context, words, parts of speech, or user expected rules to select a reading.
2. Only an explicit direct visual confirmation of a printed occurrence may contribute promotion evidence. Propagated overrides and automatic decodes never count.
3. A local occurrence override is never withdrawn merely because global promotion conflicts or fails.
4. Only complete, structurally eligible exact identities are reusable: OpenType classifies `numberOfContours >= 0` as simple, but v5.8 global TTF admission further requires `numberOfContours > 0` and hashes the complete raw record; a zero-contour glyph has no visible outline for direct visual actual evidence. CFF is style group + complete resolved glyph-recording SHA-256.
5. `full_signature`, annotation components, normalized outline, component similarity, font name, font index, glyph index, and xref are not global reuse keys.
6. Any incompatible direct reading disables reusable truth for that identity until explicit audited resolution.
7. Unknown schema, invalid identity, or unprovable cache compatibility fails closed. A present-but-unreadable store is not treated as an empty store.
8. A global update invalidates only PDFs whose exact dependency roster includes the changed identity.
9. The full session PDF roster, reconciliation, regression, and completion gates remain unchanged.
10. Global provenance and timestamps are audit metadata, not accidental cache boundaries.
11. A global write failure cannot make the UI claim global promotion succeeded; it also cannot erase a valid project-local correction.
12. No release number, tool version, or source hash substitutes for a semantics epoch or evidence fingerprint.

## 4. Project-local/global boundary

| Class | Data | Rule |
|---|---|---|
| A — forever project-local | `manual_actual_staging.json`; occurrence overrides; manifest/session; DB events; current ledger; workbook/candidate/report; project-local singles, exact records and provenance | They describe one project, one occurrence, or one transaction. Global success never replaces or deletes them. |
| B — eligible global exact evidence | Canonical reading from directly checked occurrences with either a proven-simple complete TTF `glyf` identity or a complete CFF exact identity, plus the immutable direct-evidence records required by the global quorum | It becomes reusable only after the global promotion policy and approval pass. Composite or malformed TTF records never enter this class. |
| C — provenance only | `full_signature`; CFF component/signature sequence; normalized outline; component IDs; font name/index/xref/gid; page/coordinates; decoder result; source note; source file/program SHA; migration source hashes | These may explain, correlate, deduplicate, or audit evidence. They cannot be the glyph reuse key. The embedded-font SHA is an independence qualifier, not a glyph identity. |
| D — conflict/quarantine | Every contrary direct reading for one exact identity; imported project conflict rows; disagreements among verified project/global/static exact truth; unresolved adjudication history | These records are durable and result-affecting. They block reusable exact truth but never turn into expected evidence. |

`USER_VERIFIED_SINGLE` is a project-local candidate, not global truth. Existing `VERIFIED_EXACT_GLYPH` is also not automatically global truth because its historical source count does not prove independence.

A project may continue to retain a composite or malformed TTF raw-record hash in its existing local evidence and provenance. The global admission result must label it `UNSUPPORTED_EXACT_IDENTITY` / `NON_GLOBAL_ELIGIBLE`, set `counts_toward_global_quorum = false`, and never create reusable global truth from it.

## 5. Exact identity contract

### 5.1 TTF

`TrueTypeGlyphInspector.glyph_sha256()` currently hashes the bytes returned by `glyph_bytes()` without classifying the record. `simple_contours()` separately returns an empty result for composite glyphs and documents a corpus assumption that textbook Bopomofo components are simple. That assumption is not an identity proof.

For a composite glyph, the raw record contains component glyph IDs and transforms rather than the recursively resolved component outlines. Two embedded font programs can therefore contain the same composite record bytes while the same referenced GIDs resolve to different component outlines. Consequently, equal raw composite-record SHA-256 values do **not** prove equal cross-font visible glyphs.

The only v5.8 production-reusable TTF identity is:

```text
eligibility = GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1
kind = TTF_GLYF_SHA256
style_group = ""
glyph_sha256 = lowercase SHA-256 of the complete raw, legally parsed simple glyf record
```

The canonical encoder and every admission and decoder-lookup boundary must establish all of the following from the actual `glyf` structure:

1. The record range and complete record parse are valid.
2. OpenType structural classification treats signed `numberOfContours >= 0` as simple, but v5.8 global reusable admission requires `numberOfContours > 0` because a zero-contour glyph has no visible outline for direct visual actual evidence.
3. The record is not composite or empty; negative and zero `numberOfContours` values are always non-global-eligible.
4. `glyph_sha256` covers the complete raw record, not a header, contour subset, component list, or normalized outline.
5. Classification is derived from the parsed `glyf` bytes, never from font name, font index, GID, character meaning, or a corpus assumption. A non-empty `simple_contours()` result is not itself the canonical classification contract.

Composite, malformed, truncated, out-of-range, or otherwise unprovable records fail closed as `UNSUPPORTED_EXACT_IDENTITY` / `NON_GLOBAL_ELIGIBLE`. Their direct occurrence correction, project-local learning row, and audit/provenance may remain, but they cannot create or query `VERIFIED_GLOBAL`, cannot create promotion-ready evidence, and cannot count toward the global quorum.

`font`, `font_xref`, glyph/component index and `source_font_program_sha256` are not part of the reusable glyph key. The source font-program SHA remains only an independent-source correlation qualifier. Adding it to the key would partition the unsafe composite case by font rather than prove an exact reusable glyph, so it is not the remedy.

Future composite TTF global reuse requires a separately designed, versioned **recursive exact composite-closure identity** that binds the composite structure and transforms plus exact evidence for every recursively referenced component. v5.8 does not implement that identity, normalized outlines, or component similarity.

### 5.2 CFF

Reusable identity:

```text
kind = CFF_GLYPH_SHA256
style_group = canonical non-empty cff_style_group
glyph_sha256 = lowercase SHA-256 of the complete resolved CFF glyph recording used by v5.7
```

`CFFZhuyinInspector._recording()` calls `fontTools` `CharString.draw(RecordingPen)`, and `cff_glyph_outline_sha256()` hashes the resulting complete resolved recording. The audit therefore found no TTF-style ambiguity in which an unchanged raw component-GID reference could resolve to a different outline in another font program. The composite key remains `(style_group, glyph_sha256)`; it is not changed to a font-program key. `full_signature`, body/tone/neutral signatures, annotation components, font name, font index, CID/glyph ID and source font-program SHA are audit/correlation metadata only.

### 5.3 Current consistency gap to close before global writes

The persistent CFF learning and decoder lookup correctly use `(style_group, complete glyph SHA)`, but current `build_actual_review_groups()`, `build_actual_group_for_entry()` and `group_id` group by `(kind, exact_key)` and do not include `style_group`. A rare same-SHA/different-style case could therefore share a review group even though its reusable storage key is different.

Global admission must never trust the current `group_id` as proof of CFF identity. It must recompute the full identity for every directly checked member and require all tuples to match. Phase 1 must centralize the canonical identity encoder before any global repository is created. If project staging `group_id` is changed to include CFF style, that is an explicit staging identity/schema migration with a fail-closed adapter for pending v5.7 staging—not an unversioned rewrite.

## 6. Proposed global storage

The sole recommended production location is:

```text
%LOCALAPPDATA%\DiscoveryRay\ZhuyinProofreader\GlobalExactGlyphLibrary\library.sqlite3
```

Rationale:

- `%LOCALAPPDATA%` is per-user, normally writable, persistent across application restarts, and intended for machine-local application state.
- It is outside the repository, project outputs, runtime asset directory, and current working directory.
- Git checkout/reset and `runtime_asset_manifest.json` cannot adopt or replace it.
- Keeping it local avoids network-filesystem locking ambiguity and unreviewed cross-user trust.
- One database provides atomic multi-table changes, uniqueness constraints, durable conflict/provenance records, and Windows multi-process locking more safely than coordinating several CSV/JSON replacements.

The code must resolve this path in one narrow service boundary. Tests must pass an explicit `global_library_root: Path` (or repository object constructed from it) pointing to an isolated temporary directory. Tests must never alter `%LOCALAPPDATA%`, rely on `cwd`, or monkey-patch a module-global path after the store has opened.

This audit does not create the directory or database.

At pipeline start, the service should validate and materialize one immutable `GlobalExactGlyphSnapshot` containing effective trusted/quarantined identities plus an audit generation. Decoder and fingerprint producer must share that same snapshot. Loading all compact truth states under a short SQLite read transaction is preferable to holding a database read lock through the entire PDF run.

## 7. Proposed schema

### 7.1 Physical structure

Use one SQLite database, with SQLite-managed `library.sqlite3-wal` and `library.sqlite3-shm` sidecars while open. Do not manually hash, copy, or atomic-replace the live database file. Backups use SQLite's backup API.

Recommended strict tables:

| Table | Required purpose and fields |
|---|---|
| `library_meta` | Singleton row: `schema_version`, `identity_contract_version`, `promotion_policy_version`, monotonic `generation`, `created_at`, `updated_at`. |
| `glyph_truth` | `glyph_id` (deterministic canonical identity hash), `identity_contract_version`, `identity_eligibility`, `kind`, `style_group`, `glyph_sha256`, `state`, nullable `active_reading`, `direct_source_count`, `independent_source_count`, `revision`, `created_at`, `updated_at`; unique `(kind, style_group, glyph_sha256)`. |
| `source_evidence` | Deterministic `evidence_id`, `glyph_id`, canonical `reading`, `evidence_class`, derived `counts_toward_global_quorum`, `source_project_id`, `source_pdf_sha256`, `source_font_program_sha256`, `source_occurrence_id`, `source_review_id`, `decision_snapshot_sha256`, `confirmation_channel`, `confirmed_at`, `ingested_at`. |
| `promotion_approval` | `approval_id`, `glyph_id`, canonical `reading`, immutable `quorum_digest`, `promotion_policy_version`, approval source, `approved_at`; unique active approval per identity/revision. |
| `glyph_conflict` | `glyph_id`, durable `status`, canonical conflicting-readings payload, first/last event times, open/resolved generations, optional resolution reference. Conflict history is not deleted on resolution. |
| `provenance_event` | Deterministic `event_id`, `transaction_id`, `glyph_id`, `event_type`, optional reading, referenced evidence/approval IDs, canonical payload digest, `created_at`. Append-only through the service. |
| `processed_intent` | Project outbox `intent_id`, payload digest, committed generation, result state and receipt digest. It makes retry after a crash idempotent. |
| `migration_candidate` | Deterministic import ID, glyph identity/reading, legacy project/evidence file hashes, old level, candidate/conflict/insufficient status, import time. It never counts as direct evidence by itself. |

TTF and CFF share `glyph_truth`, but strict checks express their distinct contracts:

- `TTF_GLYF_SHA256` requires empty `style_group`, 64 lowercase hex `glyph_sha256`, and `identity_eligibility = GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1`. Admission and target lookup must both obtain that result from a legal parse of the complete current raw record; a hash alone is insufficient proof.
- `CFF_GLYPH_SHA256` requires non-empty canonical `style_group`, 64 lowercase hex `glyph_sha256`, and `identity_eligibility = GLOBAL_ELIGIBLE_COMPLETE_CFF_RECORDING_V1`; the font-program SHA remains outside the reusable key.
- Other `kind` values are rejected; `SESSION_STABLE_KEY` and `OCCURRENCE_ONLY` are never stored globally.

Allowed `evidence_class` values are finite. Only globally eligible `DIRECT_VISUAL_ACTUAL` evidence counts toward promotion. `LEGACY_PROJECT_CANDIDATE` and `PROPAGATED_PROJECT_OCCURRENCE` are audit-only; expected-derived classes do not exist. A composite/malformed TTF result is retained, if desired, in project-local provenance or a non-reusable migration diagnostic; the global service rejects it before a `glyph_truth`/`source_evidence` mutation.

Counts in `glyph_truth` are transactionally derived caches of `source_evidence`. Validation must recompute and reject drift instead of trusting a hand-edited count.

### 7.2 Strict validation

Every open must validate before returning any reusable record:

1. SQLite header/open succeeds; `PRAGMA quick_check` and `foreign_key_check` pass.
2. `PRAGMA user_version` and `library_meta.schema_version` are known and agree exactly or have an explicit tested adapter.
3. Required tables, columns, indexes, uniqueness and check constraints match the declared schema. Unknown schema transitions are not guessed.
4. There is exactly one meta row; generation/revisions are non-negative and internally consistent.
5. Every SHA is lowercase 64-hex; every reading is already canonical and valid; every state/transition/eligibility enum is known. A TTF reusable row must carry the exact simple-glyf eligibility contract; unsupported/composite/malformed TTF identities cannot be silently skipped or accepted.
6. Identity hashes, quorum digests, event IDs, `counts_toward_global_quorum` and cached counts recompute exactly.
7. Conflicted identities have at least two distinct canonical readings and no active reusable reading.
8. Trusted identities have a valid approval whose quorum still satisfies the recorded policy and contains only globally eligible exact identities.

An invalid row makes the present store invalid; loaders must not skip the row and return a partial truth map.

### 7.3 Logical compatibility digest

Cache fingerprints use a canonical logical subset, not database bytes, WAL bytes, timestamps, notes, provenance volume, or the global generation counter. For each requested identity the payload includes an explicit row even when absent:

```json
{
  "identity_contract_version": "1.0",
  "promotion_policy_version": "1.0",
  "requested_identity": ["TTF_GLYF_SHA256", "", "<sha256>"],
  "identity_eligibility": "GLOBAL_ELIGIBLE_SIMPLE_GLYF_V1|NON_GLOBAL_ELIGIBLE",
  "effective_state": "NON_GLOBAL_ELIGIBLE|ABSENT|VERIFIED_GLOBAL|QUARANTINED_CONFLICT",
  "active_reading": "",
  "conflicting_readings": []
}
```

This makes absent -> trusted, trusted -> conflict, reading changes, policy changes, and deletion result-affecting. Adding matching provenance or a third source without changing effective state does not invalidate a PDF.

## 8. Promotion policy

### 8.1 Comparison

| Option | Assessment |
|---|---|
| A — one direct confirmation | Rejected. One typo or visual misread immediately contaminates future projects. |
| B — two occurrences in one project | Rejected globally. This is close to current project-local semantics and may be two copies in one PDF/font or one grouped review action. |
| C — two independent projects/sources | Better, but a vague composite-ID inequality can still count a copied PDF or the same embedded font twice; automatic promotion also lacks a final scope check. |
| D — correlation-resistant quorum plus explicit approval | Recommended. It makes source independence testable, keeps incomplete evidence as candidates, and binds promotion to an immutable reviewed quorum. |

### 8.2 Unique recommended threshold

An identity may enter `VERIFIED_GLOBAL` only when all conditions hold:

1. At least two `DIRECT_VISUAL_ACTUAL` evidence rows have the same canonical reading.
2. The quorum contains at least two distinct sealed project identities, two distinct PDF SHA-256 values, and two distinct embedded-font-program SHA-256 values.
3. The occurrences and review decisions are distinct and were among `checked_occurrence_ids`; affected/propagated peers do not count.
4. Every decision was rebuilt from the current ledger, bound to a valid manifest and artifact hashes, and contains a complete globally eligible exact identity. A TTF member must independently pass the simple-glyf structural gate; matching raw composite hashes never count.
5. No project/global/static exact contradiction or open quarantine exists.
6. An explicit promotion approval confirms the reading and source samples. The approval stores a digest of the exact evidence IDs, identity, reading and policy version.

If embedded font identity is missing, if two projects are copies with the same sealed project ID, or if the PDFs/fonts are identical, the evidence remains a candidate. Do not weaken the condition by counting a third occurrence from the same correlation domain.

This policy is intentionally stricter than project-local `VERIFIED_EXACT_GLYPH`. v5.8 metrics should measure whether the threshold is practical before any later policy change.

## 9. Independent source identity

Use the existing sealed session `session_id` as `source_project_id`. It survives v5.7 refresh/repair through cross-version identity carry-forward, and copying a project preserves the ID rather than falsely creating a second project.

For each direct confirmation, compute:

```text
source_project_id             = sealed manifest.session_id
source_pdf_sha256             = sealed/current PDF SHA-256
source_font_program_sha256    = SHA-256 of extracted embedded font program bytes
source_occurrence_id          = current occurrence_id
source_review_id              = current review_id
exact_identity                = canonical TTF or CFF tuple
decision_snapshot_sha256      = frozen decision/group/current-evidence snapshot

evidence_id = SHA-256(canonical JSON of:
  evidence schema version,
  source_project_id,
  source_pdf_sha256,
  source_font_program_sha256,
  source_occurrence_id,
  exact_identity,
  canonical reading,
  decision_snapshot_sha256)
```

The evidence ID gives deterministic idempotency, but independence is not defined as merely “different evidence ID.” A quorum must separately satisfy distinct project, PDF and font-program axes. Thus:

- confirming one occurrence twice produces the same event/no new source;
- two occurrences in one PDF do not form a global quorum;
- copying a project/PDF does not form a global quorum;
- changing only a filename/path does not form a global quorum;
- two different PDFs that embed the same font program still do not form the recommended quorum;
- font name, xref and font index cannot fake independence.

The embedded font SHA is source-correlation metadata only. It does not change the reusable glyph identity.

## 10. Conflict/quarantine model

Recommended states:

```text
ABSENT
  -> CANDIDATE
  -> PROMOTION_READY
  -> VERIFIED_GLOBAL

CANDIDATE | PROMOTION_READY | VERIFIED_GLOBAL
  -> QUARANTINED_CONFLICT

QUARANTINED_CONFLICT
  -> RESOLUTION_PENDING
  -> VERIFIED_GLOBAL  (only explicit adjudication + fresh valid quorum)
  -> RETIRED           (never reusable)
```

Rules:

- Matching direct evidence appends provenance and may advance the candidate/quorum state.
- Any new direct canonical reading different from any retained direct reading atomically opens/updates `QUARANTINED_CONFLICT`, clears `active_reading`, revokes the current approval, increments revision/generation, and records both readings and all source IDs.
- Further evidence never silently closes quarantine.
- Resolution never deletes the contrary evidence. It records an adjudication and requires a fresh policy-valid quorum bound to the resolution revision.
- Last-writer-wins is forbidden.

For “global says ㄅ; new direct visual confirmation says ㄆ”:

1. The new occurrence receives its project-local `ㄆ` override.
2. Global `ㄅ` stops being reusable immediately after the global transaction commits.
3. The identity becomes durable global quarantine with `ㄅ|ㄆ` and both evidence sets.
4. Relevant PDF caches change through their subset digest; unrelated PDFs do not.
5. This remains an actual-evidence conflict. Expected is not consulted or changed.

A valid conflict is a successful safety outcome, not a transaction failure.

## 11. Evidence precedence

Recommended evaluation order:

1. **Occurrence-specific direct manual override.** Highest authority for that printed position.
2. **Exact-identity conflict gate.** Union project and global quarantine plus cross-source disagreement checks. A conflict suppresses every reusable exact donor for that identity, including project/global/static donors; it does not suppress the occurrence override.
3. **Project-local `VERIFIED_EXACT_GLYPH`.** Only when non-conflicting.
4. **Global `VERIFIED_GLOBAL`.** Only an exact identity/state from the validated snapshot.
5. **Static exact TTF full-glyf truth.** Only when non-conflicting.
6. **Existing decoder-derived evidence.** Preserve all current CFF symbol, exact map, transform, outline, shape, bridge and symbol-recombination gates and their internal order.
7. **Unknown / abstention.** `ACTUAL_UNRESOLVED` when no independent evidence succeeds.

Before choosing levels 3–5, compare all present verified exact readings. Agreement is reinforcing; disagreement opens/quarantines the identity instead of selecting the nominally higher row. Thus “precedence” cannot hide contradictory truth.

A quarantined exact identity may still be decoded by an independent existing decoder path if that path passes its own v5.7 gates. That result does not resolve or overwrite the exact conflict. If no such evidence exists, the occurrence remains `ACTUAL_UNRESOLVED`, and the existing completion gate remains authoritative.

Project `USER_VERIFIED_SINGLE`, global `CANDIDATE`, `PROMOTION_READY`, migration candidates, provenance, and audit metadata are never reusable.

## 12. Fingerprint/cache design

### 12.1 Required contract

Add a new required actual fingerprint component, separate from project dynamic hashes:

```text
global_exact_glyph_evidence_hashes = {
  dependency_contract_version,
  identity_contract_version,
  promotion_policy_version,
  scope_mode,
  ttf_subset_sha256,
  cff_subset_sha256,
  conflict_subset_sha256
}
```

It must be declared in the same `FINGERPRINT_COMPATIBILITY_REQUIRED_KEYS["actual"]` registry used by producer and comparator. Missing or different required values fail closed. The snapshot generation may appear as audit metadata, but it must not enter the compatibility digest because that would invalidate every PDF after any global write.

### 12.2 Per-PDF scoping

Reuse the exact roster already available from `actual_workbook_dynamic_dependencies()`, but do not mistake the existing TTF SHA column for proof of global eligibility:

- for TTF, revalidate the current embedded-font raw record through the canonical parser. Query global truth only for a legally parsed simple record; emit a deterministic `NON_GLOBAL_ELIGIBLE` dependency result for composite/malformed/unprovable records;
- for CFF, request every canonical `(style_group, CFF_GLYPH_SHA256)`;
- hash the snapshot's logical state for exactly those identities, including explicit `ABSENT` rows.

Example:

- PDF A roster contains X; PDF B roster does not.
- A's subset payload includes X, so X promotion/quarantine/reading changes its fingerprint.
- B's payload does not include X, so its fingerprint remains byte-for-byte identical.

The dependency roster must include globally eligible exact keys that were unresolved when the workbook was written; otherwise a later global promotion could not invalidate them. Its identity-contract version also binds the TTF structural eligibility gate. A composite TTF hash is never looked up or promoted merely because the legacy workbook recorded it.

### 12.3 New and legacy workbooks

- New PDF/no workbook: reuse is already impossible. Decode against the immutable global snapshot, classify each encountered TTF record from its actual structure, record eligible exact keys and typed non-eligible results, then rewrite only fingerprint metadata with the final per-PDF subset, following the existing two-stage project-dynamic pattern. Do not hash the full global database as a provisional boundary.
- Current v5.7 workbook: its exact columns provide raw dependency candidates but cannot prove TTF simple-glyf eligibility. Its stored actual fingerprint also lacks the new required global contract. The recommended first v5.8 integration has no general 2.9.0 adapter; it fails reuse once, rebuilds from the current embedded-font structures, and seals the new identity/subset contract.
- Older workbook missing exact columns: distinguish “legacy contract missing” from “corrupt workbook.” Do not silently conflate exceptions with an empty roster. It must re-decode; corruption still fails artifact validation.
- Present global store cannot be read/validated: do not reuse an old workbook and do not silently treat the store as empty.

`run_pipeline_pdfs()` still receives the complete session PDF list. The existing loop remains responsible for one decode at most per invalidated PDF, CFF whole-batch evidence, candidate reuse, the whole-book manifest, ledger, reconciliation, regression and completion gates.

## 13. Version/schema/epoch impact

| Contract | Decision | Reason |
|---|---|---|
| `ACTUAL_DECODER_SEMANTICS_EPOCH` | **No bump for this design** | Global truth is a new versioned actual evidence input. With the same complete evidence set and same precedence rules, pronunciation semantics do not change. Its state is captured by the required fingerprint. If implementation later changes interpretation of existing inputs, that is a separate epoch decision. |
| `EXPECTED_RESOLVER_SEMANTICS_EPOCH` | **No bump** | Expected is not involved. |
| Actual fingerprint schema | **Bump required**; recommended new incompatible family such as `3.0.0` | Old components cannot prove the global subset/policy contract. Do not use release number as the boundary. |
| Actual `reuse_policy` | **New explicit value required**, e.g. `evidence_assets_with_global_exact_v1` | The evidence repertoire and required compatibility contract changed. |
| Workbook schema | **No bump in the minimal read-only integration, conditional on re-parsing TTF structure** | The existing TTF/CFF exact columns remain dependency candidates and the fingerprint JSON metadata can carry the new contract, but legacy TTF SHA alone never proves simple eligibility. Re-parse the current embedded font before global lookup/reuse. If implementation instead persists a new required eligibility column/layout, bump the workbook schema explicitly. |
| Session/ledger/review identity schemas | **No bump for global read reuse** | Occurrence/review identity and whole-session layout do not change. |
| Global library schema | **Required**, exact `1.0` plus `identity_contract_version` and `promotion_policy_version` | The mutable external evidence store needs its own strict lifecycle and adapters. |
| Manual staging schema | **Conditional explicit bump** | Required only if the CFF group/group-ID contract is corrected to include style. Pending v5.7 decisions then need an explicit adapter or fail-closed re-review. |

Tool version and source hashes remain audit metadata. The global database is dynamic user evidence and must not be added to `runtime_asset_manifest.json`.

## 14. Transaction design

### 14.1 Options

| Design | Risk |
|---|---|
| Put project CSV files and global SQLite in one nominal transaction | Rejected. There is no real atomic commit across filesystem replacements and SQLite; rollback after workbook refresh can create evidence/artifact disagreement. |
| Commit project, then directly write global without durable intent | Rejected. A crash between commits loses promotion work, and retry status is ambiguous. |
| Project-local transactional outbox, then independent idempotent global transaction | Recommended. It preserves the current project boundary and makes every partial state recoverable and truthful. |

### 14.2 Recommended sequence

1. Revalidate the whole staged batch exactly as v5.7 does.
2. Build global promotion intents only from frozen `checked_occurrence_ids`; validate exact identity and source metadata.
3. In the project-local transaction, update the existing five evidence files **and** atomically persist a strict `_專案證據/actual/global_exact_glyph_promotion_outbox.json` intent set. The outbox becomes an explicit additional local transaction member in that future phase.
4. Acknowledge manual staging only when that project transaction succeeds.
5. Deliver all pending intents in deterministic order to global SQLite with one `BEGIN IMMEDIATE` transaction. Validate the complete intent batch before mutation. Valid reading disagreements commit as quarantine outcomes.
6. Record `processed_intent` receipts and one global generation bump. Atomically mark matching project outbox tokens delivered; a crash before local acknowledgement simply retries the same intent IDs.
7. Clear project actual-dependent events once and perform one full-session incremental refresh.

Status must have separate fields such as `project_actual_commit`, `global_promotion_delivery`, and `project_refresh`. There is no single ambiguous “all saved” boolean.

### 14.3 Failure/crash outcomes

| Point | Durable truth and recovery |
|---|---|
| Project apply fails | Existing five files/outbox roll back; staging remains. No global write. |
| Project apply succeeds; global write fails | Local actual is valid; outbox remains pending/failed with reason. Continue project refresh. UI must not claim global promotion. Project completion need not be blocked by this cross-project optimization. |
| Global write succeeds; project refresh fails | Project evidence and global result remain committed. Report both as committed and the refresh as failed; use existing `--refresh-actual` recovery. Never roll either evidence store back after possible workbook writes. |
| Crash before global commit | SQLite rolls back; outbox retries. |
| Crash after global commit but before outbox acknowledgement | `processed_intent` makes retry a deterministic no-op that returns the original receipt. |
| Partial project file write | Existing byte snapshots plus outbox snapshot restore the complete local transaction. |
| Partial global write | SQLite WAL transaction commits all tables or none. |

## 15. Concurrency design

Two projects/processes must be assumed to write the same identity concurrently.

Minimum safe Windows design:

1. SQLite is stored on local `%LOCALAPPDATA%` and opened in WAL mode with foreign keys enabled.
2. Reads use a short consistent transaction to materialize an immutable snapshot.
3. Writes use `BEGIN IMMEDIATE`, a bounded `busy_timeout`, and a bounded retry policy. Exhausted lock contention returns an explicit pending/locked result; it never falls back to an unlocked write.
4. `processed_intent.intent_id` and payload digest provide idempotency. Reusing an ID with different bytes is a hard error.
5. Every `glyph_truth` row has a `revision`. Updates use compare-and-swap (`WHERE revision = expected_revision`) inside the transaction; a miss triggers re-read/reconciliation, not overwrite.
6. `library_meta.generation` increments once per committed global batch. It is used for diagnostics/snapshot consistency, not as a global cache key.
7. SQLite's OS file locks and WAL are the file-locking/transaction journal. Do not add a second ad-hoc lock-file protocol for normal database writes.
8. SQLite commit is the atomic operation; manual `Path.replace()` of the live DB is forbidden. Export/backup files may use temp + atomic replace only after SQLite backup completes.
9. Append one provenance event per effective transition and one transaction ID per batch.

If two writers propose the same reading, unique evidence IDs merge sources and recompute quorum. If they propose different readings, the serialized second transaction observes the first and commits quarantine. Neither writer wins by timing.

## 16. Migration design

Existing project `VERIFIED_EXACT_GLYPH` rows must not be copied into `VERIFIED_GLOBAL`:

- `source_examples` contains occurrence IDs but not independently validated project/PDF/font axes;
- two sources may be the same PDF;
- current provenance may include propagated members;
- historical rows do not prove which members were directly checked;
- old CFF group IDs do not encode style.

Recommended migration is an explicit candidate-import command/workflow:

1. Select a project and strictly validate its sealed manifest, artifact hashes, current ledger, project actual schemas and exact identities.
2. Import each local single/verified row as `LEGACY_PROJECT_CANDIDATE`, with the project/session ID, exact project evidence file hashes, old verification level, reading and import provenance.
3. Do not set `counts_toward_quorum` and do not create promotion approval.
4. Import project conflict rows immediately as global quarantine candidates/safety evidence; never discard them because their source count is incomplete.
5. When multiple candidate imports agree, retain all but remain insufficient until direct sources are freshly verified under the new contract.
6. When candidates disagree, quarantine the identity.
7. Allow the GUI/CLI to reopen original occurrence samples for fresh direct visual confirmation. Only the new confirmation events can count.

`migration_import_id` is a hash of migration schema version, sealed project ID, source file SHA-256, exact identity, reading and legacy row key. Re-running the same import is a no-op; a changed source file is a new auditable import, never an overwrite. Absolute paths are not global identity and should not be persisted as reusable evidence.

## 17. Failure behavior

| Condition | Required behavior | Completion impact |
|---|---|---|
| Global store does not exist | Treat as an explicit known `ABSENT`/disabled global lane; continue with v5.7 project/static/decoder evidence. Do not create it during a read-only check. | Does not block by itself. A workbook that previously depended on global gets a different subset/mode and cannot reuse. |
| Present store corrupted / failed integrity check | Do not rename, recreate, skip bad rows, or continue as empty. Preserve bytes and report recovery/backup guidance. | Fail actual source validation and block new output/completion mutation. |
| Unknown schema/identity/promotion contract | Require an explicit adapter/migration; no partial read. | Fail actual validation. |
| Valid direct row conflicts with trusted/candidate reading | Atomically quarantine, preserve direct occurrence override and all readings/provenance. | Not a global transaction failure. Relevant occurrences re-evaluate; unresolved rows keep completion non-terminal. |
| Structurally duplicate DB identity row | Unique constraint/integrity failure; do not guess a winner. | Store invalid; block actual validation. |
| Same identity + same reading + same intent/evidence ID | Idempotent no-op returning the existing receipt. | No block. |
| Same identity + same reading + new eligible source | Insert source, recompute quorum; change cache only if effective state changes. | No block. |
| I/O permission denied while reading existing store | Do not treat as absence or reuse old global-dependent cache. | Fail actual validation until access is restored. |
| Permission denied / file locked while delivering promotion | Keep project outbox pending after bounded retry; report global promotion failure separately. | Project-local apply/refresh/completion may proceed because local actual remains authoritative. |
| Partial/orphan `.tmp` file | Never adopt it as the library. SQLite uses WAL recovery; orphan export/backup temp is diagnostic only. | Main DB absent follows ABSENT policy; malformed main/WAL follows corruption policy. |
| Invalid Bopomofo in incoming intent | Reject whole intent batch before mutation. | Project actual remains; global delivery pending/error. |
| Invalid SHA/style/kind in incoming intent | Reject whole intent batch before mutation. | Same as above. |
| Invalid Bopomofo/SHA already stored | Store corruption; never skip the row. | Fail actual validation. |
| Global snapshot changes during one pipeline run | Continue using the immutable start snapshot. Next run compares the newer subset. | Current run remains internally consistent. |

## 18. Future GUI

No GUI is implemented in this audit. The minimum future UX should distinguish four independent facts:

1. **Global reuse source:** e.g. `actual 來源：Global exact TTF glyf SHA-256` or `Global exact CFF（樣式群組 + 完整字形 SHA-256）`, with identity/source count available in technical details.
2. **Global conflict:** reusable exact truth stopped; direct local reading retained; show the conflicting actual readings and affected identity without suggesting an expected answer.
3. **Global promotion pending:** project correction is complete, but evidence lacks independence/approval or the outbox has not delivered.
4. **Global promotion success/failure:** show project apply, global delivery and project refresh as separate statuses and provide retry for delivery/refresh without reapplying staging.

Use “actual 字形證據” consistently. Do not use a dictionary icon, “建議讀音”, “應標”, or wording that could imply global reuse is an expected judgment. Promotion approval must show only direct PDF visual samples and source-correlation facts, not expected values.

## 19. Metrics

Metrics are aggregate audit telemetry and must not become evidence:

| Metric | Definition/use |
|---|---|
| Exact global reuse hit rate | Occurrences decoded from `VERIFIED_GLOBAL` / occurrences carrying a complete exact identity. |
| Global rescue rate | Previously unresolved exact occurrences resolved solely by a trusted global exact record. |
| Manual actual review reduction | Comparable pending actual reviews avoided after global reuse, with a pre-global baseline and no expected-derived attribution. |
| Cross-project exact SHA match rate | Current-project exact identities already present as global candidate/trusted/conflict. |
| Promotion funnel | Direct evidence -> independent quorum -> approval -> trusted counts and reasons for remaining candidate. |
| Same-source correlation rejection rate | Candidate pairs rejected because project, PDF, or font-program identity was not distinct. |
| Time to independent quorum/promotion | From first direct evidence to quorum and approval. |
| Global conflict rate | Identities entering quarantine / identities with direct evidence. |
| False promotion rate | Trusted identities later quarantined by contrary direct evidence / promoted identities. This is the primary safety metric and should approach zero. |
| Cache invalidation precision | PDFs invalidated by a global change / all cached PDFs, plus identities causing each invalidation. |
| Unrelated-cache stability | PDFs whose dependency roster excludes the changed identity and retain identical fingerprints. |
| Store reliability | Validation failures, permission/lock failures, retry latency and pending outbox age. |
| Migration conversion | Legacy candidates freshly re-confirmed and promoted / imported candidates. |

v5.9 normalized-outline evaluation should use these metrics to determine whether exact identity coverage is insufficient. A low exact-hit rate does not authorize weaker matching if conflict/false-promotion data is not yet trustworthy.

## 20. Test matrix

| Area | Required tests |
|---|---|
| Root resolution | Production default is exact `%LOCALAPPDATA%` path; explicit temporary root wins; no `cwd`, repository or project leakage; read-only lookup does not create. |
| Schema creation | Empty v1 DB creates exact tables/constraints/meta; database starts at generation 0. |
| Strict schema | Missing/extra required structure, duplicate meta, unknown `user_version`, unknown state, invalid cached count and identity digest all fail closed. |
| Corruption | Truncated DB, malformed WAL, failed `quick_check`, foreign-key violation and invalid row block all reusable reads. |
| TTF simple eligibility | OpenType still classifies `numberOfContours >= 0` as simple, but v5.8 global admission requires a legally parsed visible simple `glyf` with `numberOfContours > 0` and hashes its complete raw record. Zero-contour, composite, malformed and truncated records are `NON_GLOBAL_ELIGIBLE`. Classification comes from structure, not font name/index, GID, semantics or corpus assumptions. |
| TTF composite ambiguity | Construct two synthetic fonts with byte-identical composite records but different outlines at the referenced component GID; they must never produce, count toward, query or reuse the same global exact glyph truth. Project-local evidence/provenance remains allowed. |
| TTF/CFF identity separation | A TTF eligible-simple exact SHA is independent of font name/index. CFF uses `style_group` + the resolved complete `RecordingPen` recording SHA; same CFF SHA with different style does not reuse. Neither key includes font-program SHA. |
| Current CFF gap | Global admission rejects a live group whose members do not share `(style, SHA)`; any staging identity migration is explicitly tested. |
| Phase 1 project regression | With no global library, existing project-local actual pronunciation behavior remains unchanged except for the separately reviewed CFF grouping identity hardening; composite/malformed global ineligibility cannot erase local corrections. |
| Actual/expected separation | Promotion/read modules cannot import expected resolver/dictionary modules; expected fields in intent/store payload are rejected; actual decisions do not inspect expected. |
| Candidate policy | One direct source stays candidate; two occurrences in one PDF stay candidate; copied project/PDF stays candidate; same embedded font stays candidate. |
| Independent quorum | Two distinct project + PDF + font sources with same reading become promotion-ready; missing any axis does not. |
| Approval | Quorum without approval is non-reusable; approval binds exact evidence digest; stale quorum/revision approval fails. |
| Idempotency | Same evidence/intent retry is a no-op with same receipt; same ID/different payload fails. |
| Project/global precedence | Direct occurrence override wins; project/global/static agreement reuses; any disagreement triggers conflict gate rather than nominal priority. |
| Conflict quarantine | Trusted ㄅ + direct ㄆ retains new local override, revokes global reuse, stores both readings, invalidates relevant subset and never reads expected. |
| Conflict resolution | No automatic recovery; audited resolution + fresh quorum required; history remains. |
| Decoder fallback | Quarantine suppresses exact donors but preserves independently gated decoder behavior; otherwise `ACTUAL_UNRESOLVED`. |
| Logical subset hash | Absent/trusted/conflict/reading/policy changes alter relevant digest; timestamps, notes, source-count growth without state change and provenance do not. |
| Per-PDF scoping | X change invalidates PDF A containing X; PDF B without X remains reusable; CFF style is part of scoping. |
| Old v5.7 workbook | Exact columns can build current roster, but missing new required fingerprint contract causes one-time no-reuse/rebuild; no broad compatibility guess. |
| Older/malformed workbook | Missing dependency columns causes conservative re-decode; malformed protected workbook fails integrity, not silent empty dependency. |
| Full session integration | `run_pipeline_pdfs()` still receives every session PDF; each invalidated PDF decodes once; unaffected actual/candidate workbooks reuse; CFF whole-batch pool remains. |
| Snapshot consistency | Concurrent global commit during a pipeline cannot mix old fingerprint with new decoder evidence; next run sees the newer subset. |
| Atomic global write | Inject failure after each table mutation; no truth/evidence/conflict/provenance/generation partial commit. |
| Project outbox | Project five files + outbox roll back together; staging remains on failure; only checked IDs create intents. |
| Delivery failures | Project success/global failure leaves pending outbox; global success/local ack crash retries idempotently; global success/refresh failure reports truthful split state. |
| Multi-process same reading | Two processes serialize, preserve both sources, meet quorum once and bump generation once per committed batch. |
| Multi-process different reading | Interleaving always ends quarantined with both readings; never last-writer-wins. |
| Lock/permission | Bounded lock retry; read denial blocks actual validation; write denial keeps promotion pending without corrupting project actual. |
| Restart | Trusted/conflict/pending outbox and receipts survive process restart; no session-memory-only state. |
| Migration | Existing verified rows import as untrusted candidates; conflict imports quarantine; rerun is idempotent; changed source hash is a new audit event. |
| Runtime boundaries | Global DB never appears in runtime asset manifest; no runtime asset bytes/SHA change; source validation remains intact. |
| Epoch/version | Global evidence change affects required fingerprint; tool/source audit drift alone does not; actual/expected epochs remain independent. |
| Completion gates | Global candidate/failure never fabricates PASS; unresolved/conflict states still flow through ledger, reconciliation, regression and completion gates. |

Tests must simulate at least two project roots and two concurrent processes on Windows. Concurrency tests should use deterministic barriers/events, not `sleep`.

## 21. Recommended phased implementation

Each phase has one main objective and its own review/validation gate.

### Phase 1 — Exact identity contract hardening

Objective: stabilize the one canonical exact-identity contract shared by project and future global code before any global repository exists.

- Add the canonical TTF identity encoder and its legal-parse/simple-glyf global eligibility gate. Composite or malformed records return a typed non-global-eligible result and never count toward a global quorum.
- Canonicalize CFF identity as `(style_group, complete resolved glyph-recording SHA-256)` and explicitly close the current CFF review-group/group-ID consistency gap.
- If the CFF correction changes manual staging `group_id` or schema, provide an explicit version, tested adapter or fail-closed migration; never reinterpret pending v5.7 staging silently.
- Do not create SQLite, resolve `%LOCALAPPDATA%`, read global evidence, write global evidence, or change a decoder pronunciation result because of a global library.
- Gate: simple/composite/malformed TTF identity tests, CFF style/group tests, staging migration tests if applicable, and proof that existing project-local actual behavior remains unchanged except for the explicitly reviewed CFF grouping hardening.

### Phase 2 — Global repository foundation

Objective: establish a strict, injectable, concurrency-safe global storage and immutable read-snapshot foundation without connecting it to decoding or project promotion.

- Implement the exact `%LOCALAPPDATA%` resolver, SQLite v1 schema, strict validation, immutable snapshots, logical subset digest primitives and backup/recovery diagnostics.
- Implement WAL, `BEGIN IMMEDIATE`, bounded busy timeout, CAS/revision and idempotency primitives.
- Do not consume global evidence in the decoder and do not write promotion evidence from a project.
- Gate: root/schema/corruption/logical-subset/Windows multi-process tests; production pronunciation output remains unchanged.

### Phase 3 — Read-only exact reuse and per-PDF fingerprint scoping

Objective: allow only manually seeded/test-fixture `VERIFIED_GLOBAL` exact records to participate in actual decoding with precise cache invalidation.

- Add the required global subset fingerprint component, new actual fingerprint schema/reuse policy, snapshot injection, TTF simple-glyf lookup gate and source labels.
- Preserve complete session refresh, decoder ordering, conflict gate, expected separation and one-decode-per-invalidated-PDF behavior.
- Do not write/promote global evidence from project reviews yet.
- Gate: eligible-simple TTF/CFF exact reuse, composite/malformed TTF rejection, precedence/conflict, v5.7 one-time rebuild and per-PDF cache tests.

### Phase 4 — Transactional promotion delivery and global conflict writes

Objective: deliver new directly checked project evidence safely to the global repository.

- Add the strict project-local transactional outbox to the project batch transaction.
- Implement independent-source quorum, explicit approval, idempotent SQLite batch delivery, CAS, conflict state machine, receipts and split-status recovery.
- Only `checked_occurrence_ids` can produce `DIRECT_VISUAL_ACTUAL` evidence.
- Gate: project/global failure matrix, crash/idempotency, concurrent same/different reading and refresh-failure semantics.

### Phase 5 — Auditable legacy candidate migration

Objective: import existing project learning as non-reusable candidate/conflict evidence without bulk promotion.

- Add explicit project selection, full source validation, deterministic import IDs, candidate/conflict states and fresh-reconfirmation workflow.
- No automatic trusted import, no broad filesystem scan, no change to promotion threshold.
- Gate: multi-project migration, duplicates, corrupted project, rerun and candidate-to-fresh-evidence tests.

### Phase 6 — Operational GUI observability and metrics

Objective: expose source/conflict/pending/success/failure and safe retry states without changing evidence semantics.

- Add global-source labels, promotion approval, conflict view, outbox delivery retry, refresh retry and metrics described above.
- Keep actual UI visually and structurally separate from expected/dictionary UI.
- Gate: deterministic Tk layout/worker/restart tests, wording assertions, no per-item refresh threads, full project gates and real-textbook acceptance.

No later phase starts until the preceding phase has independent review and its required regression suite is green.

## 22. Explicit non-goals

- No production implementation in this audit.
- No composite TTF global reuse in v5.8. Any future support requires a separately versioned recursive exact composite-closure identity binding structure, transforms and all recursively referenced component evidence; adding `source_font_program_sha256` to the reusable key is not a substitute.
- No normalized-outline global identity or reuse; that is at most a v5.9 research question.
- No component similarity/learning; that is at most a v5.10 research question.
- No fuzzy/nearest-neighbor matching, semantic inference, word/character meaning, font-name/index matching, or annotation-component global promotion.
- No expected/dictionary/candidate-equality path into actual evidence.
- No automatic import of existing `VERIFIED_EXACT_GLYPH` as global trusted truth.
- No replacement/removal of project-local evidence after global promotion.
- No cloud/shared-account library, network synchronization, multi-user trust federation, or repository-tracked database.
- No runtime asset manifest entry, runtime asset SHA change, or static asset mutation.
- No occurrence/review identity migration as a side effect.
- No partial-PDF call to `run_pipeline_pdfs()` and no weakening of per-PDF fingerprints.
- No semantics epoch bump based on release number.
- No weakening of source validation, artifact integrity, regression, reconciliation or completion gates.

The safe v5.8 boundary is deliberately narrow: global reuse of complete exact glyph identities backed by independently sourced, direct human actual evidence, with durable quarantine and precisely scoped cache invalidation.
