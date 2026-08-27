# Hash / Fingerprint Architecture Audit — v5.6.2

- Repository: `discoveryray/zhuyin-proofreader`
- Branch: `audit/hash-fingerprint-architecture`
- Baseline / audited commit: `c55f6f36bd6a6a8e00d70e47330933c05bbbd614` (`v5.6.2`)
- Audit mode: architecture and documentation only; no production behavior, test, asset, manifest, SHA value, resolver, repair, regression-gate, identity, or cache logic changed
- Audit date: 2026-08-27

## 1. Executive Summary

This audit found **36 named hash / fingerprint / signature mechanism families** in the current repository. The count treats the 20 independently pinned runtime-asset SHA values as one architecture family, and separately counts distinct producers or consumers when the same digest algorithm serves a different contract. Three requested concepts do **not** exist as standalone digests: there is no whole-font fingerprint, no final-report SHA, and no single session-compatibility hash. Session compatibility is instead a composition of session/schema identity, chain fingerprints, workbook hashes, snapshots, and semantic reconciliation.

The architecture is not suffering from “too many SHA values.” Most digests protect different layers and should remain. The strongest current properties are:

1. Runtime assets are fail-closed by exact raw-byte SHA plus schema/roster validation. The 20-asset roster is explicit: 11 actual and 9 expected.
2. Actual and expected cache chains are separated. Expected-asset changes do not invalidate actual decode caches; actual changes rebuild only the affected downstream candidates.
3. Dynamic actual evidence is scoped to the exact TTF/CFF glyph dependencies of each PDF when a current workbook exposes those dependencies. Unrelated glyph learning therefore does not normally invalidate unrelated PDFs.
4. Candidate authoritative evidence is hashed independently of Excel formatting, while the complete actual/candidate workbooks are also byte-sealed in the session manifest.
5. `occurrence_id` and `review_id` are durable identity digests, not cache fingerprints. They intentionally exclude actual/expected readings and normally exclude Excel row order.

The most important risk is **under-invalidation after a semantic code change**. v5.6 intentionally makes decoder/resolver versions and source-code hashes audit-only. The test suite explicitly requires source changes alone to preserve the reuse fingerprint. Consequently, a future decoder or resolver algorithm change that changes results without changing evidence assets can reuse stale actual or candidate workbooks. This is a high architectural risk path, although no specific stale result was demonstrated in this audit.

Two additional compatibility details matter before adding an epoch:

- `fingerprint_schema_version` and `reuse_policy` are recorded, but `fingerprint_compatible()` ignores them on its semantic fallback path.
- Merely adding an epoch to `reuse_components` would therefore be insufficient. A future epoch must be present in both audit components and reuse components **and must be explicitly compared by the semantic compatibility function**.

Recommendation: retain all runtime asset integrity SHA values; add no epoch in v5.6.2; in a future compatibility release introduce separate `actual_decoder_semantics_epoch` and `expected_resolver_semantics_epoch`, backed by explicit comparison and golden-equivalence tests. Keep review identity on its exact schema boundary rather than treating it as a cache epoch.

## 2. Scope / Method

The starting working tree was clean. The checked-out branch and `HEAD` were verified before analysis:

```text
branch: audit/hash-fingerprint-architecture
HEAD:   c55f6f36bd6a6a8e00d70e47330933c05bbbd614
tag:    v5.6.2
```

The audit searched all 106 tracked files, including 46 Python files (26 tests), runtime JSON/CSV/TXT data, `.gitattributes`, changelog/audit history, and all callers/consumers. Broad searches covered at least `sha256`, `hashlib`, `fingerprint`, `hash`, `signature`, `digest`, `candidate_payload_sha256`, `occurrence_id`, `review_id`, `pdf_sha256`, `source_hashes`, `artifact`, `manifest`, `reuse_policy`, `dynamic_actual_hashes`, `glyph_sha256`, and `outline_signature`.

The repository contains 10 production Python modules importing `hashlib`:

```text
actual_review.py
cff_zhuyin_decoder.py
check_pronunciation_candidates.py
export_pdf_text_diagnostics.py
export_zhuyin_readings.py
occurrence_ledger.py
revision_diff.py
runtime_source_validation.py
standalone_proofread.py
ttf_zhuyin_shape_decoder.py
```

Callers and compatibility consumers in `cross_version_compat.py`, `review_gui.py`, `standalone_gui.py`, `cff_zero_map_batch.py`, and the tests were also traced. For each family below, the audit followed producer → stored representation → consumer → comparison point → mismatch result → invalidated layer.

Role notation used throughout:

- **A — Identity**: answers whether two records refer to the same object, occurrence, review, rule, or group.
- **B — Integrity**: detects changed or damaged bytes/payloads.
- **C — Cache Compatibility**: determines whether an old computed result may be reused.
- **D — Audit / Provenance**: records the code/source/version in force without itself deciding reuse.
- **E — Glyph / Geometry Evidence Identity**: identifies exact glyph bytes, outlines, or component geometry.
- **MULTI_ROLE**: the same digest crosses two or more of these boundaries.

Recommendations are future-facing only: `KEEP`, `KEEP_BUT_CLARIFY`, `SPLIT_ROLE`, `CONSIDER_REMOVE`, `FUTURE_SEMANTICS_EPOCH`, or `NEEDS_TEST`.

## 3. Complete Hash & Fingerprint Inventory

### Table A — complete inventory

| ID / Name | Producer | Inputs / output form | Stored In | Consumer | Role | Chain | Change / invalidation scope | Recommendation |
|---|---|---|---|---|---|---|---|---|
| H01 Runtime asset SHA-256 (20 pinned values) | `runtime_source_validation.py:63-68`, `validate_asset_manifest():305-427` | Exact file bytes → 64 lowercase hex | `runtime_asset_manifest.json`; validation report `assets[].sha256/expected_sha256` | Startup, pipeline, report/regeneration, fingerprint builders, tests | **B+C+D MULTI_ROLE** | 11 actual / 9 expected | Any unapproved byte mismatch blocks the main pipeline globally. An approved manifest-consistent actual change invalidates all actual fingerprints; expected change invalidates all expected fingerprints. | **KEEP**. Removing it loses the primary asset integrity boundary. |
| H02 Pipeline PDF SHA-256 | `runtime_source_validation.sha256_file`, `standalone_proofread.sha256_file`, `export_zhuyin_readings._sha256_file`, candidate direct hasher | Exact PDF bytes → 64 hex | Actual workbook metadata, candidate ledger, session `pdfs[]`, records, reports | Actual fingerprint, PDF relocation, occurrence identity, candidate validation, repair | **A+B+C MULTI_ROLE** | Shared, originates in actual input | Any byte change invalidates one PDF’s actual/candidate outputs and changes its occurrence/review identities. | **KEEP**; clarify that metadata-only PDF rewrites are intentionally treated as new source identity. |
| H03 Historical regression `source_pdf_sha256` | Regression CSV authoring; current PDF H02 is comparator | Pinned source PDF digest → 64 hex | `pronunciation_regressions.csv`, candidate regression rows | `historical_regression_applicability()` and regression gate | **A+B MULTI_ROLE** | Expected | Exact hard gates apply only to matching PDF bytes; mismatch becomes not-applicable rather than selecting a similar occurrence. CSV asset SHA also protects this field. | **KEEP**. |
| H04 `actual_decoder_source_hashes` | `compute_actual_asset_fingerprint():465-471` | Raw bytes of 9 listed decoder-related `.py` files → path→64-hex map | Actual fingerprint `components` in actual workbook metadata | Manual audit; `metadata_compatibility_warnings()` can compare but no actual caller uses it | **D** | Actual | Change does **not** invalidate actual cache. Raw checkout EOL can change the value across platforms. | **KEEP_BUT_CLARIFY + NEEDS_TEST**; surface warnings and maintain the source roster. |
| H05 `expected_resolver_source_hashes` | `compute_expected_asset_fingerprint():562-568` | Raw bytes of 6 listed resolver-related `.py` files → path→64-hex map | Candidate summary components and session expected components | `metadata_compatibility_warnings()`; value is computed in report generation but the returned warning list is currently unused | **D** | Expected | Change does **not** invalidate candidate reuse. | **KEEP_BUT_CLARIFY + NEEDS_TEST**. |
| H06 `cff_batch_algorithm_hash` | `compute_actual_asset_fingerprint():497` | Alias of H04 entry for `cff_zero_map_batch.py` → 64 hex | Actual fingerprint components | No named consumer found | **D** | Actual | No independent invalidation; duplicates an H04 value. | **CONSIDER_REMOVE** only in a future schema migration, or document as a convenience alias. |
| H07 `consensus_hash` | `compute_actual_asset_fingerprint():498` | Alias of H01 actual asset `cff_crossfamily_cid_consensus` → 64 hex | Actual fingerprint components | No named consumer found | **D** (underlying value is B+C) | Actual | No independent invalidation; duplicates `actual_asset_hashes`. | **CONSIDER_REMOVE** only with migration; do not remove the underlying asset SHA. |
| H08 Actual asset fingerprint | `compute_actual_asset_fingerprint():451-512` | Canonical JSON of `reuse_policy`, H02, actual H01 map, scoped H11 map → 64 hex | Actual workbook metadata; session `pdfs[].actual_asset_fingerprint` | `output_is_reusable`, candidate analysis, `collect_manifest`, repair pipeline | **C+D MULTI_ROLE** | Actual | PDF/actual assets/relevant dynamic evidence invalidate one or all applicable actual workbooks. Source/version drift does not. | **FUTURE_SEMANTICS_EPOCH**; otherwise **KEEP**. |
| H09 Expected asset fingerprint | `compute_expected_asset_fingerprint():550-587` | Canonical JSON of `reuse_policy` and expected H01 map → 64 hex | Candidate summary, session, GPT export metadata, compatible-fingerprint allowlist | Candidate reuse, report generation, GPT expected import, repair | **C+D MULTI_ROLE** | Expected | Expected asset change invalidates all candidates; source/version drift does not. | **FUTURE_SEMANTICS_EPOCH**; otherwise **KEEP**. |
| H10 Dynamic actual raw-file hashes | `validate_dynamic_actual_evidence():159-207` | Exact bytes of override/user-TTF/user-CFF/conflict CSVs; absent override is literal `ABSENT` | Transient validation report; returned by `dynamic_actual_hashes(root)` without PDF | Tests/diagnostics; production per-PDF fingerprint path computes them during validation but returns H11 | **B+D** | Actual | Raw hash change alone is not the production per-PDF key. Schema/content validation failure blocks fingerprint creation globally. | **KEEP_BUT_CLARIFY**; raw digests are not the scoped cache key. |
| H11 Scoped dynamic actual evidence hashes | `dynamic_actual_hashes():279-338`, `_subset_sha256()` | Canonical JSON row subsets for manual overrides, user TTF truth, user CFF truth, and conflict registry → four 64-hex values | H08 components/reuse components in actual workbook | Actual cache compatibility | **C+B MULTI_ROLE** | Actual | Per-PDF when dependencies exist; legacy/global fallback includes all learned glyph/conflict rows. | **KEEP**; consider semantic-field-only subhashing in a later design. |
| H12 Dynamic `scope_mode` SHA | `dynamic_actual_hashes():333` | Literal `global_fallback` or `per_pdf_exact_dependency_v1` → 64 hex | H11 mapping | Outer H08 fingerprint | **C+D** | Actual | Changes fingerprint representation if mode changes, but semantic fallback compares only the mapping as a whole and therefore sees this value. | **KEEP_BUT_CLARIFY**; a plain versioned contract string would be clearer. |
| H13 TTF raw `glyf` SHA-256 | `TrueTypeGlyphInspector.glyph_sha256():178-180` | Exact raw `glyf` record bytes → 64 hex | Actual workbook `TTF字形SHA256`, static/dynamic fingerprint/transform/correction/conflict tables | Exact bridge, transform gate, mapping correction, user learning, quarantine, dependency extraction | **E+C MULTI_ROLE** | Actual | Selects glyph-specific truth and H11 dependencies; byte differences conservatively prevent reuse even if rendered outlines look alike. | **KEEP**. |
| H14 CFF whole-glyph SHA-256 | `cff_glyph_outline_sha256():169-178` | `repr(tuple(recording))` of the complete rendered CFF glyph program → 64 hex | Actual workbook `CFF整字字形SHA256`, user-CFF truth/conflict/provenance | `(style_group, glyph_sha256)` exact learning and quarantine; H11 dependency extraction | **E+C MULTI_ROLE** | Actual | Relevant learned truth/conflict invalidates PDFs containing the exact style+glyph key. | **KEEP**. |
| H15 TTF normalized outline/component signature | `contour_group_signature():66-78` | Translation-invariant canonical contours including width/height and on-curve flags → SHA-1, 40 hex | Actual workbook outline fields; `ttf_verified_outline_signatures.csv`; symbol template assets | Exact outline lookup, shape model, symbol/tone recombination | **E** | Actual | Geometry algorithm/table change can alter decoding; asset table change already invalidates actual cache, algorithm-only change currently does not. | **KEEP + FUTURE_SEMANTICS_EPOCH**; do not reinterpret as file integrity. |
| H16 CFF component signature | `cff_zhuyin_decoder._signature():124-147` | Translation-normalized, 0.001-rounded CFF contour operations → SHA-1, 40 hex | Actual workbook body/tone/neutral signature fields; CFF symbol map | Exact CFF component mapping and batch bootstrap | **E** | Actual | Identifies annotation component geometry, not a cache or file-integrity digest. | **KEEP + FUTURE_SEMANTICS_EPOCH**. |
| H17 CFF full annotation signature | `cff_full_annotation_signature():181-194` | Style group + ordered H16 body signatures + tone/neutral signatures → SHA-256 | Actual workbook, user-CFF audit column, review groups | Audit/group context; legacy workbooks without H14 remain occurrence-specific | **E+D MULTI_ROLE** | Actual | Must not become the reusable CFF truth key; different Han glyphs may share annotation components. | **KEEP_BUT_CLARIFY**. |
| H18 `occurrence_id` | `make_occurrence_id():228-257` | H02 + page + sanitized stable key + font/xref/glyph/component + char + coordinates; optional source-row fallback → `occ_` + 64 hex | Actual/candidate workbooks, session records, DB/event links, reports | Reconciliation, grouping, imports, review identity producer | **A** | Crosses actual→expected, but is neither cache fingerprint | Normal rows stable across Excel reorder. PDF or identity field change creates a new occurrence. | **KEEP**; never treat as a cache fingerprint. |
| H19 `review_id` | `make_review_id():260-262` | Exact `REVIEW_ID_SCHEMA_VERSION|occurrence_id` → `rev_` + 64 hex | Candidate/session/DB/GPT workbooks/reports | Durable event lookup and import mapping | **A** | Shared | Changes only with occurrence or exact review-ID schema. Exact schema mismatch blocks session/import/repair. | **KEEP**. |
| H20 Actual review `group_id` | `actual_review._hash_payload`, group builders | `{kind, exact_key}` → `agr_` + first 24 hex (96 bits) | Actual GPT workbook/package; transient group objects | Group lookup and transaction validation | **A** | Actual | Exact glyph/group identity change creates a new group. | **KEEP**. |
| H21 Actual review `group_snapshot` | Actual group builders | Group id/kind/key and member occurrence/review/state/actual evidence fields → full 64 hex | Hidden actual GPT workbook column | `import_actual_review_workbook()` | **A+B MULTI_ROLE** | Actual | Any relevant group/member drift rejects the row as stale/modified. | **KEEP**. |
| H22 Glyph provenance `event_id` | `_append_provenance_event():761-793` | Kind/style/glyph/readout/event type/occurrence IDs/source/note → `gpe_` + first 24 hex | `glyph_truth_provenance.csv` | Deduplicates audit events | **A+D MULTI_ROLE** | Actual audit | Does not enter H11 and does not invalidate runtime. | **KEEP_BUT_CLARIFY**. |
| H23 Full `review_snapshot` | `_review_snapshot():748-766` | Session/review schema, occurrence/review IDs, state, actual+expected evidence/context → 64 hex | Hidden expected/GPT workbook column | Non-expected actions reject drift; expected-only actions use mismatch only as safe-rebase telemetry | **A+B MULTI_ROLE** | Cross-layer | Actual or expected drift blocks actions depending on both lanes. | **KEEP**; current split is intentional. |
| H24 `expected_snapshot` | `_expected_review_snapshot():769-799` | Expected/context lane plus occurrence/review/schema → 64 hex | Hidden expected/GPT workbook column | No current comparison consumer found; compatibility helper is also unused | **D / legacy staleness token** | Expected | No runtime invalidation in current import path. | **CONSIDER_REMOVE** only after format-compatibility review; otherwise label audit-only. |
| H25 `expected_target_snapshot` | `_expected_target_snapshot():802-849` | Printed occurrence identity/context, excluding actual and automatic expected output → 64 hex | Hidden expected/GPT workbook column | Expected-only safe rebase | **A+B MULTI_ROLE** | Expected | Target/context drift rejects import; actual/resolver drift alone may safely rebase. | **KEEP**. |
| H26 Exported actual-evidence text SHA-256 | `_sha_text():1082-1083` | UTF-8 text of current `actual_evidence` → 64 hex | `actual_occurrence_decisions.csv` inside GPT bundle | Actual occurrence decision import | **B** | Actual | Evidence drift rejects visual-decision import even if occurrence mapping remains. | **KEEP**. |
| H27 `candidate_payload_sha256` | `candidate_payload_sha256():765-812`; candidate writer | Canonical authoritative ledger, mandatory regression rows, PDF regression rows → 64 hex | Candidate workbook summary | `collect_manifest()` recomputes before accepting candidate | **B+D MULTI_ROLE** | Cross-layer candidate | Authoritative evidence change rejects candidate; Excel styling is excluded. | **KEEP**. |
| H28 Actual workbook SHA-256 | `collect_manifest():2103` | Entire XLSX bytes → 64 hex | Session `pdfs[].actual_workbook_sha256` | Session load, GUI, report, reuse baseline, repair | **B** | Actual artifact | Any byte change, including style/ZIP metadata, breaks the old seal. | **KEEP**; presentation separation would require a new explicit design. |
| H29 Candidate workbook SHA-256 | `collect_manifest():2104` | Entire XLSX bytes → 64 hex | Session `pdfs[].candidate_workbook_sha256` | Baseline candidate reuse, session/report/repair validation | **B+C MULTI_ROLE** | Expected artifact | Any byte change blocks old session/repair; candidate reuse requires exact bytes. | **KEEP**. |
| H30 Session `manifest_integrity_sha256` | `manifest_integrity_sha256()/seal_manifest():1562-1577` | Canonical JSON of every manifest field except itself, including H28/H29 and records → 64 hex | `校對工作階段.json` | GUI, imports, report, regeneration, repair, baseline reuse | **B+D MULTI_ROLE** | Shared/session | Any manifest edit/truncation blocks use until a trusted rebuild re-seals it. | **KEEP**. |
| H31 Reusable-rule snapshot SHA-256 | `reusable_rules_sha256():220-223` | Canonical session snapshot of approved reusable expected rules, including provenance fields → 64 hex | Session `reusable_expected_rules_sha256` beside the snapshot | `materialize_ledger()` | **B+D MULTI_ROLE** | Expected session layer | Snapshot tamper blocks materialization. Root rule-file change does not invalidate base candidate H09; a new session captures and applies the new snapshot. | **KEEP_BUT_CLARIFY**. |
| H32 Reusable expected `rule_id` | `build_reusable_rule():264-288` | Rule content plus provenance/timestamp → `USER-EXPECTED-` + first 20 hex (80 bits) | `可重用expected規則.json`, session snapshot, evidence | Rule dedup/display/audit | **A+D MULTI_ROLE** | Expected | New rule content creates a new ID; save path preserves an existing ID for an identical condition/readings tuple. | **KEEP**. |
| H33 `expected_gap_group_key` | `expected_gap_group_key():482-494` | Exact char/line/position/context/actual/candidate phrase → SHA-1, 40 hex | Transient dictionary while exporting grouped GPT gaps | GPT export grouping only | **A** | Expected workflow | Changes grouping only; no cache/session invalidation. | **KEEP_BUT_CLARIFY**. |
| H34 `semantic_review_key` | `semantic_review_key():419-435` | Source sheet, text/context, actual/expected/rule evidence and visibility → SHA-1 | Not written by current production path | No current production consumer | **A / legacy** | Legacy | No current effect. | **CONSIDER_REMOVE** after confirming external callers do not rely on it. |
| H35 `semantic_review_key_v1` / built-in decision-memory keys | `semantic_review_key_v1():440-460`; pre-v5.2 data | Older exact-context tuple → SHA-1; existing 40-hex JSON keys | `校對判定記憶.json` and function return | `load_decision_memory()` returns `{}` and `apply_decision_memory()` is disabled | **A+D / legacy** | Legacy isolated | No active evidence, cache, or completion effect. | **KEEP_BUT_CLARIFY** as quarantined history, or archive in a future cleanup. |
| H36 Revision-diff PDF cache SHA-256 | `revision_diff.sha256_file()`, `_cache_ok():104-111` | Exact PDF bytes → 64 hex | Per-side `解碼快取.json` | `revision_diff.decode_revision()` | **C+B MULTI_ROLE** | Separate revision-diff utility | Reuses decoded workbook only when tool version, PDF name, and bytes match. It does not use the main H08 asset fingerprint. | **SPLIT_ROLE + NEEDS_TEST** if this utility becomes production-critical. |

### Non-hash compatibility markers

`fingerprint_schema_version`, `actual_ledger_schema_version`, `decoder_version`, `resolver_version`, `dynamic_dependency_mode`, and `reuse_policy` are stored alongside H08/H09. They are valuable audit metadata, but the current semantic fallback in `fingerprint_compatible()` compares only:

- actual: `pdf_sha256`, `actual_asset_hashes`, `dynamic_actual_evidence_hashes`;
- expected: `expected_asset_hashes`.

Therefore `fingerprint_schema_version`, ledger version, and `reuse_policy` are **not effective hard reuse boundaries** once the direct fingerprint comparison fails. This must be addressed by any future compatibility-contract change.

## 4. Identity Hashes

### 4.1 Occurrence identity

H18 is generated from:

```text
PDF SHA-256
+ physical page
+ stable key with Excel/source-row tokens removed
+ font / font_xref / glyph_id / zhuyin_component_id
+ character
+ x0/y0/x1/y1 canonicalized to 0.001
```

Actual and expected readings, evidence, workflow state, candidate category, and Excel row order do not participate. This is the correct separation: H18 answers “is this the same printed occurrence?”, not “is this cached result still reusable?”.

The normal confidence is `PDF_STABLE`. If the PDF hash plus all discriminators cannot identify a row, the caller must explicitly provide `fallback_source_row_number`, producing `FALLBACK_SOURCE_ROW`. When initially identical base IDs collide, the first row keeps the canonical PDF identity and later indistinguishable layers are forced to row fallback and carry `identity_collision_base`. Duplicate occurrence and review IDs fail closed.

Consequences:

- Normal occurrences are stable across Excel row reordering.
- Fallback identities are deliberately order/source-row sensitive and may change when ambiguous source layers are reordered.
- Any PDF-byte change changes all H18 values for that PDF, even if the byte change is non-rendering metadata. This is conservative source identity, not accidental cache coupling.

### 4.2 Review identity

H19 is `SHA-256(REVIEW_ID_SCHEMA_VERSION | occurrence_id)`. It does not include candidate category, actual, expected, or state. The same occurrence therefore retains its review key while it moves between views or evidence states.

`REVIEW_ID_SCHEMA_VERSION` is compared exactly, unlike ordinary session/workbook schemas where patch-level drift within a major/minor family is allowed. Review events, GPT imports, actual-review packages, report regeneration, and repair all depend on this exact boundary. A future occurrence/review algorithm change must bump the exact identity schema and provide an adapter; it must not be hidden inside a cache epoch.

### 4.3 Workflow identity tokens

H20/H21 identify and seal actual-review groups. H23/H25 seal the reviewed object and its current semantic target. H32 identifies reusable expected rules. H33 groups only exact expected gaps during export. H34/H35 are legacy identity keys with no current runtime consumer.

These are identity/staleness tokens, not general cache keys. Removing them would lose transactional import safety or durable event addressing, even if the underlying session remained byte-sealed.

## 5. Integrity Hashes

### 5.1 Runtime asset integrity

`runtime_asset_manifest.json` defines 20 required assets:

- **Actual (11):** `zhuyin_component_map`, `font_compatibility_groups`, `cff_bopomofo_symbol_map`, `cff_crossfamily_cid_consensus`, `ttf_xref_component_overrides`, `ttf_verified_component_transforms`, `ttf_verified_glyf_fingerprints`, `ttf_verified_outline_signatures`, `ttf_verified_mapping_corrections`, `ttf_verified_symbol_templates`, `structural_detection_exclusions`.
- **Expected (9):** `moe_concise_dictionary`, `project_polyphonic_dictionary`, `pronunciation_lexical_rules`, `handbook_pronunciation_rules`, `handbook_pronunciation_constraints`, `pdf_occurrence_regressions`, `mandatory_regression_cases`, `character_overrides`, `source_context_overrides`.

Validation checks strict JSON parsing without duplicate keys, exact required name/chain roster, unique names/paths, path containment, existence, SHA format, exact raw-byte SHA, file parser/schema, required columns, row count, unique/composite keys, nonempty requirements, and Bopomofo canonicalization where configured.

An SHA mismatch adds an error stating that runtime will not automatically accept a new hash. The main pipeline checks the overall validation report before building either chain and writes `pipeline_blocked.json` / `pipeline_status.json` with `SOURCE_INVALID`. Thus one unapproved asset mismatch is a global fail-closed condition in the main tool, even though the lower-level fingerprint builders can validate one chain independently.

This global failure is intentionally stronger than cache invalidation. It should not be relaxed merely to reduce SHA maintenance.

### 5.2 Candidate and workbook sealing

H27 separates authoritative candidate evidence from presentation noise. It hashes canonical ledger fields, confirmation gates, mandatory regression results, and historical PDF regression results. Excel formatting, column width, cell styles, and ZIP container metadata are excluded. `collect_manifest()` reparses the workbook and rejects it if the recomputed H27 differs from the stored summary value.

H28/H29 then hash the entire actual/candidate XLSX bytes. H30 seals the session manifest that contains those hashes and all authoritative records. This nested structure is intentional:

```text
authoritative candidate payload --H27--> candidate semantic seal
complete actual XLSX ------------H28--+
complete candidate XLSX ---------H29--+--> session manifest --H30--> sealed session
```

A user who changes only Excel styling changes H28 or H29. The old session then fails `validate_output_artifact_hashes()` even though H27 would be unchanged. In particular, `repair_project_state()` rejects the old project before decode/rebuild. This is strict artifact integrity, not candidate cache semantics. A normal pipeline recovery may accept and re-seal an unsealed presentation-only workbook only after `collect_manifest()` repeats all semantic/schema/set checks; repair does not bypass the old seal.

### 5.3 What is not sealed

- The final user report and technical audit workbook have no stored SHA. They are regenerated presentation outputs and are not authoritative inputs.
- `pipeline_status.json` and `待人工確認.json` are not session integrity artifacts. Code comments explicitly say pipeline status is not proof of completion, although the launcher displays it.
- `人工判定資料庫.json` has no self-hash. It is strict-JSON parsed, schema checked, keyed by known H19 values, and every event is semantically replayed through allowed transitions. A structurally valid but deliberately altered event is not cryptographically distinguishable from an authorized UI write. This is a separate mutable-event trust model, not covered by H30.
- All hashes are unkeyed. They detect accidental/casual modification and bind internal layers, but are not signatures against an adversary who can edit data and recompute every digest.

### 5.4 Repair safety

Repair performs, in order:

1. H30 validation of the existing manifest.
2. H28/H29 validation of all old output workbooks.
3. Session/ledger/review schema compatibility checks.
4. Strict loading and semantic replay of the decision database.
5. PDF relocation by filename plus H02 exact bytes.
6. Normal pipeline reuse/rebuild using H08/H09/H11.
7. Candidate payload, set reconciliation, regression, and actual/candidate cross-checks.
8. New H28/H29 values and H30 resealing while retaining the session ID.

This prevents repair from using a damaged or silently altered old actual/candidate workbook. It does not accept a new artifact hash merely because the workbook opens.

## 6. Cache Compatibility Fingerprints

### 6.1 Exact v5.6 actual reuse contract

`compute_actual_asset_fingerprint()` records these audit components:

```text
fingerprint_schema_version
pdf_sha256
actual_ledger_schema_version
decoder_version
actual_decoder_source_hashes
actual_asset_hashes
dynamic_actual_evidence_hashes
dynamic_dependency_mode
cff_batch_algorithm_hash
consensus_hash
reuse_policy
```

Only these values enter `reuse_components`:

```text
reuse_policy = evidence_assets_v1
pdf_sha256
actual_asset_hashes
dynamic_actual_evidence_hashes
```

The outer H08 is SHA-256 of canonical JSON for that subset. On cross-version fallback, `fingerprint_compatible(chain="actual")` compares only PDF, actual asset map, and dynamic actual map; it does not compare `reuse_policy`.

**A. Decoder source code changes, with identical PDF/assets/dynamic evidence:** the old actual cache is reused. H04 changes, but H08 remains the same; even an older differently constructed fingerprint may pass semantic fallback.

**C. Was this intentional?** Yes. Comments and `tests/test_cross_version_compat_v560.py` explicitly require source/tool drift not to change reuse.

**D. Safety advantages:** controller/GUI/release churn no longer destroys reviewed actual artifacts; expected-only releases leave actual untouched; raw source EOL differences do not cause cross-platform invalidation; hard boundaries remain PDF bytes, approved actual assets, relevant dynamic evidence, artifact integrity, and compatible schemas.

**E. Stale-cache candidates:** contour normalization, TTF raw-glyph parsing, transform/mapping precedence, CFF recording normalization, tone classification, bootstrap/consensus behavior, conflict/quarantine handling, structural detection, and occurrence extraction can change output without changing current reuse components.

### 6.2 Exact v5.6 expected reuse contract

`compute_expected_asset_fingerprint()` records:

```text
fingerprint_schema_version
resolver_version
expected_resolver_source_hashes
expected_asset_hashes
reuse_policy
```

Only `reuse_policy` and `expected_asset_hashes` enter the direct fingerprint; semantic fallback compares only the asset map.

**B. Resolver source code changes, with identical expected assets:** the old candidate can be reused if actual was also reused and the candidate artifact hash/schema remain valid.

**C. Was this intentional?** Yes, for the same reviewed-work preservation goal.

**D. Safety advantages:** GUI/controller/release changes do not rebuild every candidate; expected and actual chains stay independent; verified old workbooks can remain import-compatible through `compatible_expected_asset_fingerprints`.

**E. Stale-cache candidates:** Bopomofo canonicalization, dictionary parsing, rule priority/selection, phrase-position matching, conflict resolution, mandatory regression semantics, historical regression applicability, and candidate ledger generation can change results without changing H09.

### 6.3 Reusable expected rules are a separate layer

`可重用expected規則.json` is not in the runtime asset manifest and does not enter H09. The base candidate workbook may therefore be reused after this rule file changes. During `collect_manifest()`, the current canonical rule document is copied into the session, H31 is computed, and `materialize_ledger()` applies that immutable session snapshot before replaying review events.

This is safe under the current architecture because reusable rules are a post-candidate session layer. It is not evidence that H31 is a candidate cache key. If rule application ever moves into candidate generation, the compatibility contract must change.

## 7. Audit / Provenance Hashes

H04/H05 preserve implementation provenance without invalidating caches. H06/H07 duplicate a named subset for audit convenience. H22 provides idempotent provenance-event identity but is excluded from dynamic actual cache fingerprints. H31 records the exact reusable-rule snapshot that was active in the session.

Two clarity issues were found:

1. Actual source drift has no caller that produces a compatibility warning.
2. Expected report generation calls `metadata_compatibility_warnings()` and assigns `compat_warnings`, but the value is never written into either report or status output.

Thus source hashes are stored but are not currently visible to normal users. This does not change reuse behavior, but weakens their stated audit value. A future documentation/report-only change should surface them without turning every source-byte change into cache invalidation.

Source hashes are hashes of raw checkout bytes. With `core.autocrlf=true`, Python files are LF in Git and CRLF in this Windows checkout, so the audit hash can differ by platform even for the same Git blob. Keeping them audit-only is therefore safer than using them directly as compatibility keys.

## 8. Glyph / Geometry Signatures

The glyph signatures have different meanings and must not be collapsed:

| Signature | Exact input | Primary meaning | Cache/integrity meaning |
|---|---|---|---|
| H13 TTF raw-glyf SHA-256 | Raw bytes of one `glyf` record | Exact glyph byte identity across embedded subsets/fonts | Indirectly selects relevant dynamic evidence; not a file-integrity seal |
| H14 CFF whole-glyph SHA-256 | Complete recorded CFF glyph program | Exact full-glyph visual/program identity, namespaced by style group in lookup | Indirectly selects relevant dynamic evidence |
| H15 TTF outline/component SHA-1 | Translation-invariant normalized contour group | Exact geometry identity for whole outline or symbol/tone component | No direct cache role; algorithm semantics must be versioned in future |
| H16 CFF component SHA-1 | Translation-normalized/rounded component contours | Exact Bopomofo/tone component identity | No direct cache role |
| H17 CFF full annotation SHA-256 | Style + ordered H16 component signatures | Compact annotation evidence identity | Audit/group context only; not reusable CFF truth key |
| TTF symbol-template signature | H15 algorithm plus template type | Exact isolated symbol/tone/neutral template identity | Asset table bytes are protected by H01 |

There is **no standalone whole-font fingerprint**. Font name, style group, xref, glyph ID, raw glyph bytes, and normalized outlines provide the actual evidence dimensions. This avoids making a whole embedded font file the invalidation unit when only one glyph matters.

The exact raw TTF SHA is intentionally reused by transform gates, mapping corrections, cross-font bridges, user truth, conflicts, and per-PDF dependency selection. This is useful coupling around one exact object identity. It is also conservative: equivalent visible outlines encoded with different hinting/point order/bytes do not share H13. H15 provides a separately governed geometry-equivalence route.

H15/H16 use SHA-1 as compact exact lookup identifiers, not as adversarial cryptographic integrity. There is no demonstrated collision problem in the current trusted local tables. A future learning-scale migration may choose a domain-separated SHA-256 signature, but that would require versioned table regeneration, regression coverage, and an actual semantics boundary; it is not a v5.6.2 cleanup.

## 9. Cross-layer Dependency Map

```text
runtime actual asset bytes --H01--+
project dynamic actual rows --H11-+--> H08 actual compatibility --> actual XLSX --H28--+
PDF bytes ------------------H02--+                 |                               |
                                                    +--> H13/H14/H15/H16/H17         |
                                                    +--> H18 occurrence --> H19 review
                                                                                     |
runtime expected asset bytes --H01--> H09 expected compatibility ----------------+  |
actual ledger + expected resolver + regressions -------------------------------> candidate XLSX
                                                                                 H27 semantic seal
                                                                                 H29 byte seal
                                                                                     |
reusable expected rule document ----------------H31 session snapshot----------------+
review DB events ---------------- schema/identity/transition validation --------------+
                                                                                     |
all session fields + H28/H29 -----------------------------------------------------> H30
                                                                                     |
                                                                                     +--> reports/status
                                                                                          (not hash-sealed)

decoder/resolver source bytes --> H04/H05 --> audit components only
```

Actual and expected are independent until candidate construction. An expected asset change does not change H08. An actual change forces the affected candidate to rebuild because the candidate consumes actual rows, even though H09 itself is unchanged.

## 10. Invalidation Matrix

The table describes current behavior before a trusted rebuild. “Integrity block” means the old sealed project/output is rejected, not merely that a cache miss occurs. For runtime assets, an unapproved bytes-only change blocks globally; a manifest-consistent approved release follows the chain-specific rebuild shown in parentheses.

### Table B — invalidation matrix

| Change | Actual cache invalidated? | Expected candidate invalidated? | Existing session compatible? | Repair allowed? | Integrity block? | Reason |
|---|---|---|---|---|---|---|
| PDF bytes change | Yes, single PDF | Yes, same PDF downstream | No | No; PDF relocation requires recorded H02 | Yes | H02 participates in H08, H18, workbook/session checks. |
| Runtime actual asset change | Unapproved: blocked; approved: yes, all PDFs | Approved: yes downstream after actual rebuild | No until rebuild | Only if runtime manifest and bytes form an approved valid release | Yes on mismatch | H01 fail-closed; actual asset map is H08 input. |
| Runtime expected asset change | No after an approved update | Unapproved: blocked; approved: yes, all candidates | Report path rejects stale H09 | Yes for approved valid runtime; rebuild expected only | Yes on mismatch | Expected asset map is H09 input; main source validation is global. |
| Unrelated dynamic actual glyph change | No with per-PDF dependency roster; yes for legacy global fallback | No unless legacy fallback redecodes actual | Yes in the controlled workflow | Yes | No, if dynamic file validates | H11 filters exact H13/H14 dependencies; fallback is conservative. |
| Relevant dynamic actual glyph change | Yes for PDFs containing the exact glyph | Yes for those PDFs after actual rebuild | Must be refreshed; external manual edit is not rechecked by report path | Yes | No, if valid | H11 changes H08. Controlled writers immediately refresh. |
| Decoder source code change | **No** | No unless actual is otherwise rebuilt | Yes if schemas remain compatible | Yes | No | H04/version are audit-only. Potential under-invalidation. |
| Resolver source code change | No | **No** | Yes if schemas remain compatible | Yes | No | H05/version are audit-only. Potential under-invalidation. |
| GUI-only change | No | No | Yes | Yes | No | GUI is outside source rosters/reuse components. |
| README change | No | No | Yes | Yes | No | Not an identity, evidence, cache, or integrity input. |
| Workbook presentation-only change | Actual XLSX: old actual artifact no longer reusable under seal; candidate-only edit need not alter actual semantics | Candidate XLSX bytes become incompatible | No under old H28/H29/H30 | Repair rejects; normal recovery must fully revalidate before reseal | Yes | Whole-XLSX hashes intentionally include presentation bytes; H27 would remain unchanged. |
| Candidate authoritative evidence change | No | Yes / candidate rejected | No | No from the tampered artifact | Yes | H27 and H29 detect semantic and byte drift. |
| Manual actual override change | Relevant scoped PDF: yes; unrelated PDF: no | Downstream for redecoded PDFs | Controlled workflow refresh required | Yes if file validates | No | Override rows are PDF-filtered H11 inputs; source/note also affect current subset hash. |
| Reusable expected rule change | No | Base candidate H09: no | Old session remains bound to its immutable snapshot; new run captures new H31 | Yes | Only if stored session snapshot/H31 is tampered | Rules are applied at materialization, after candidate generation. |

## 11. MULTI_ROLE / Coupling Findings

1. **H01 runtime asset SHA — justified MULTI_ROLE.** The same per-file digest proves raw-byte integrity, records provenance, and becomes a cache component. These roles align because an approved evidence-asset byte change should invalidate its chain. Do not split by deleting validation; at most clarify naming between `manifest_expected_sha256` and `validated_actual_sha256`.
2. **H02 PDF SHA — justified but broad MULTI_ROLE.** It is source identity, integrity locator, actual cache input, regression applicability value, and an ingredient of H18. This intentionally treats any PDF-byte change as a new source version.
3. **H13/H14 glyph digest — controlled E+C coupling.** The digest identifies visual evidence and selects which dynamic evidence rows enter H11. The digest itself is not the outer cache key.
4. **H27/H29 — deliberate semantic/byte dual seal.** H27 separates authoritative evidence from presentation; H29 still protects the complete delivered artifact. They are complementary rather than duplicate protection.
5. **H31 — integrity/provenance, not H09.** The reusable-rule snapshot is applied later than candidate generation. Naming it a “snapshot hash” is correct; naming it an expected asset fingerprint would be incorrect.
6. **H23 full review snapshot — intentionally cross-lane.** Final difference/exclusion actions depend on both actual and expected. Expected-only actions use H25 to avoid stale cross-lane coupling.
7. **H06/H07 — duplicate audit aliases.** They have no independent consumer and repeat values already present in component maps.
8. **H30 nested hashes — necessary.** The manifest hash binds the recorded workbook hashes and fingerprints so a recorded digest cannot be edited without invalidating the manifest. It is not wasted “hash of hash.”
9. **H17 hash of component signatures — necessary composition.** It creates a compact ordered annotation identity, but must remain separate from full-glyph truth H14.
10. **Version/schema/SHA overlap is not total duplication.** Schema answers whether a layout can be parsed; SHA answers whether approved bytes changed; semantics epoch would answer whether unchanged data may be interpreted by a changed algorithm. Tool version is audit only. Each boundary should stay explicit.

## 12. Potential Over-Invalidation

### 12.1 Presentation-only workbook edits

H28/H29 make styling changes an integrity failure for the old session. This is the clearest operational over-invalidation, but it prevents silently resealing a user-modified result. Severity: **Medium**, not a correctness bug. A future design could store a canonical authoritative workbook payload for both actual and candidate while treating the styled XLSX as replaceable presentation, but only after preserving all current schema/set/reconciliation checks.

### 12.2 Dynamic evidence metadata

H11 hashes every declared header in the relevant rows. Fields such as `updated_at`, `notes`, `source_examples`, `source`, and conflict provenance metadata can change without changing the actual reading decision, yet will invalidate the relevant PDF. This is limited by per-PDF/glyph scoping but is still a semantic/presentation coupling. Severity: **Medium**. Future work may split authoritative truth fields from audit metadata while retaining both in H30/provenance.

### 12.3 Legacy/global dynamic fallback

If `actual_workbook_dynamic_dependencies()` cannot read the evidence columns, H11 falls back to all user TTF/CFF/conflict rows for that PDF. An unrelated glyph edit then invalidates the legacy workbook. This is safe and should remain the fallback until the workbook is regenerated. Severity: **Low–Medium**.

### 12.4 PDF exact-byte identity

A metadata-only PDF rewrite changes H02, all H18/H19 IDs, and both output layers even if the visible pages are identical. This is conservative and prevents unproven cross-version identity reuse. Severity: **Medium operational impact**, justified for current audit safety.

### 12.5 Global runtime source validation

The main pipeline blocks on any H01 mismatch, even when only the other chain is needed by a lower-level operation. This is intentional fail-closed release integrity. It is not a recommendation to delete or loosen asset SHA checks.

No **high-risk cache over-invalidation** was found in the v5.6 per-PDF dynamic design. The main remaining cases are deliberate integrity strictness or conservative legacy fallback.

## 13. Potential Under-Invalidation

### Table C — risk register

| Finding | Severity | Current consequence | Trigger | Existing protection | Future recommendation |
|---|---|---|---|---|---|
| Semantic decoder change is audit-only | **High** | Stale actual workbook can be reused | Algorithm output changes with same PDF/assets/dynamic evidence | Artifact/schema checks and regressions, but no semantic compatibility boundary | Add explicitly compared actual decoder semantics epoch/profile. |
| Semantic resolver change is audit-only | **High** | Stale candidate can be reused | Resolver output changes with same expected assets | Candidate payload/file integrity only proves the old output was not altered | Add explicitly compared expected resolver semantics epoch/profile. |
| Semantic fallback ignores `reuse_policy` and fingerprint schema | **High (future change risk)** | A changed policy/epoch could still accept an old fingerprint | Direct digest differs, fallback sees same asset maps | Current hardcoded comparator | Compare the epoch/profile explicitly; missing/unknown values fail closed unless allowlisted. |
| Dynamic actual evidence is not re-fingerprinted during ordinary report generation | **Medium–High** | A valid external/manual dynamic CSV edit can leave an old sealed report/session apparently usable until pipeline/repair runs | Edit project evidence outside controlled UI/import flow | Controlled writers call refresh; artifact hashes protect old workbooks, not current dynamic CSV | Recompute/compare per-PDF H08 at session open/report, or document dynamic files as write-through-only. |
| Source-drift warnings are not surfaced | **Medium** | Audit-only change is silent to normal users | H04/H05 differ | Components retain values | Write warnings to technical audit/status; add tests. |
| Mutable decision DB has no self-seal | **Medium** | Structurally valid altered events can be replayed as if authorized | Deliberate edit that passes schema/transition rules | Strict JSON, exact H19 map, transition validation | Consider per-event hash chain or signed/audited write log; do not fold it into immutable H30 without a mutable-state design. |
| Dynamic hashes include audit metadata | **Medium over-invalidation** | Relevant PDF can be rebuilt for note/timestamp-only changes | Metadata field edit | Per-PDF exact scoping | Split authoritative and provenance subhashes in a future schema. |
| Whole-XLSX artifact hashes include style | **Medium over-invalidation** | Old session/repair blocks on formatting edits | Style-only save | H27 semantic candidate seal and full revalidation recovery | Keep now; study canonical actual/candidate payload seals before any relaxation. |
| H06/H07 have no consumer | **Low** | Duplicate audit fields and documentation burden | Any component creation | Underlying maps remain | Clarify or remove only in a schema migration. |
| H24 expected snapshot has no current comparison consumer | **Low** | Hidden unused digest persists in exports | Expected export | H25 is the active safe-rebase token | Confirm backward-compat consumers; then label audit-only or retire with schema bump. |
| H34/H35 legacy review keys are inactive | **Low** | Dead code/data can be mistaken for active memory | Maintenance/search | Active loaders explicitly disable legacy memory | Mark quarantined; archive only after external-call audit. |
| Revision-diff cache omits main actual assets | **Medium, isolated utility** | It can reuse decode after decoder/assets change if its own `VERSION` is unchanged | Revision utility invoked after semantics/data update without version bump | Utility version + PDF hash; later diff is diagnostic | Use H08 or a dedicated semantics profile if promoted to authoritative use. |
| SHA-1 geometry signatures are not adversarial integrity | **Low currently** | Theoretical collision could map geometry incorrectly | Untrusted/generated collision at scale | Trusted pinned tables, exact geometry workflows, regression gates | Future domain-separated SHA-256 migration only with versioned assets/epoch. |

The first three findings establish a **high-risk under-invalidation path** for future semantic changes. They do not prove that current v5.6.2 outputs are stale; they prove that the present compatibility contract cannot distinguish a semantics-changing code edit from a harmless code edit.

An additional audit-provenance limitation is that H04/H05 depend on manually maintained source-file rosters. The architecture test ensures the two actual rosters agree with each other, but does not prove transitive completeness. A newly imported semantic module can be omitted from audit hashes unless the roster/test is updated.

## 14. v5.6.2 Runtime Asset SHA Incident Lessons

The v5.6.2 incident involved 14 manifest-protected CSVs: 10 actual assets and 4 expected assets. Their schema, rows, order, fields, and semantic content were unchanged across the investigated commits. Git had normalized previously approved CRLF payloads to LF because no byte-freeze attributes existed. Every recorded manifest SHA matched the CRLF form; normalizing those restored bytes back to LF reproduced the historical Git blob SHA.

The repair correctly:

- restored the already approved CRLF bytes;
- did **not** update `runtime_asset_manifest.json`;
- did **not** accept new SHA values;
- did **not** relax schema or chain validation;
- added `.gitattributes` entries for all 18 manifest-protected CSV files using `-text whitespace=cr-at-eol`;
- added tests proving the production root validates, the 14 restored payloads normalize to the historical LF blobs, the complete manifest CSV roster is byte-frozen, and actual/expected chain membership is unchanged.

The two XLSX assets are binary and are not subject to text EOL conversion. The five project dynamic actual evidence CSVs are deliberately not in `.gitattributes` because they are mutable project evidence, not immutable runtime assets.

Current validation at the audited commit reports:

```text
schema:   2.6.2
assets:   20
actual:   11
expected: 9
errors:   0
warnings: 0
ok:       True
```

Architectural lesson: when SHA pins exact bytes, repository transport must also preserve exact bytes. The correct response to unexplained mismatch is to establish provenance and byte history, not automatically rewrite the manifest. The incident strengthens the case for retaining H01.

## 15. Semantics Epoch Suitability Analysis

### 15.1 Is `actual_semantics_epoch` genuinely needed?

**Yes, for a future semantics-changing release.** The current contract intentionally reuses actual output across all decoder source/version drift. Asset and regression protections reduce risk but cannot prove equivalence for arbitrary algorithm changes.

Preferred name: `actual_decoder_semantics_epoch` or, more descriptively, an `actual_decoder_compatibility_profile`.

It should be stored in:

1. `components` for audit;
2. `reuse_components` so the direct H08 changes;
3. the explicit `fingerprint_compatible(chain="actual")` comparison.

The third item is mandatory. Under current code, adding it only to `reuse_components` would not work across the semantic fallback.

When changed, it should invalidate all actual workbooks governed by that decoder contract, then rebuild their downstream candidates and re-seal the session/report. It should not by itself change H18/H19 unless the occurrence identity algorithm also changes.

### 15.2 Is `expected_semantics_epoch` genuinely needed?

**Yes, for a future resolver-semantics release.** Preferred name: `expected_resolver_semantics_epoch` or `expected_resolver_compatibility_profile`.

It should likewise be in both component sets and explicitly compared. A change should invalidate all base candidate workbooks, keep actual workbooks reusable, recompute regression results, replay still-valid durable decisions, and re-seal session/report state.

### 15.3 Should identity have an epoch?

Do **not** use the same cache epoch for identity. H18/H19 migrations have stronger consequences than stale computation: stored events and external workbooks must be mapped to new durable IDs. Current exact `REVIEW_ID_SCHEMA_VERSION` is the right style of boundary.

If the occurrence payload/canonicalization changes, introduce an explicit `occurrence_identity_schema_version` (and bump review identity as required), fail closed, and provide a tested adapter. An `identity_semantics_epoch` is acceptable only if it has exact-schema semantics and migration rules; it should not be treated as a simple cache invalidator.

### 15.4 Two epochs or finer sub-epochs?

For the first change, two coarse boundaries are preferable:

- `actual_decoder_semantics_epoch`
- `expected_resolver_semantics_epoch`

Finer actual profiles (TTF parser, CFF parser, batch bootstrap, structural detection) would reduce invalidation but require reliable per-workbook dependency declarations for algorithms, not merely glyph/data dependencies. Finer expected profiles (dictionary parser, lexical rule engine, regression applicability) have the same requirement. Without that proof, fine epochs risk under-invalidation.

### 15.5 Is an allowlist better than an epoch?

A structured compatibility allowlist can be better long-term, but only with fail-closed defaults. Recommended progression:

1. Add the two explicit epochs/profiles.
2. For a new release, default old/missing profile to incompatible.
3. Allow an old→new profile pair only after golden corpus and regression outputs prove equivalence.
4. Store the approved pair in code or a pinned compatibility manifest, never infer it from equal tool versions or source hashes.
5. Do not carry old `compatible_expected_asset_fingerprints` across an epoch mismatch unless that exact profile pair is allowlisted.

This is more precise than hashing source files: source hashes invalidate harmless refactors and still cannot explain semantic equivalence. Epoch/profile values express the contract directly.

## 16. Keep / Clarify / Split / Future-change Recommendations

### KEEP

- All 20 H01 runtime asset SHA values and their schema/roster checks.
- H02 PDF SHA as exact source identity.
- H08/H09 independent actual/expected fingerprints.
- H11 conflict-aware, per-PDF dynamic evidence hashing.
- H13/H14 exact glyph truth keys and H15/H16 component geometry signatures.
- H18/H19 occurrence/review identity and exact review schema compatibility.
- H21/H23/H25 transactional snapshots.
- H27 candidate authoritative payload seal.
- H28/H29/H30 artifact/session sealing.
- H31 reusable-rule snapshot integrity.

### KEEP_BUT_CLARIFY

- H04/H05 are audit-only and platform-byte-sensitive; surface their drift.
- H10 raw dynamic hashes validate/report files but are not the production scoped key.
- H12 is a policy/mode marker encoded as a digest.
- H17 is annotation evidence, not reusable whole-glyph identity.
- H22 provenance never affects reuse.
- H31 changes session materialization, not the base expected candidate cache.
- Final reports/status are regenerable outputs, not sealed evidence sources.

### SPLIT_ROLE

- Future H11 should separate authoritative dynamic truth fields from audit-only timestamps/notes.
- A future workbook format may separate canonical authoritative payload sealing from presentation artifact sealing, but must not weaken current fail-closed repair.
- Revision-diff H36 should use the main actual compatibility contract if its output becomes authoritative.

### CONSIDER_REMOVE

- H06/H07 duplicate aliases after a documented schema migration.
- H24 if backward-compatible workbook analysis confirms no external consumer.
- H34/H35 only after confirming they remain fully quarantined and no external integrations call them.

### FUTURE_SEMANTICS_EPOCH

- H08: explicit actual decoder semantics profile.
- H09: explicit expected resolver semantics profile.
- H15/H16 algorithm changes must bump the actual profile and version their tables.

### NEEDS_TEST

- Epoch/profile mismatch must defeat both direct and semantic fallback reuse.
- Missing epoch/profile behavior for old workbooks must be explicitly tested.
- Source-drift warnings must appear in the technical audit if retained.
- External dynamic-evidence edits must either invalidate session/report or be explicitly unsupported and detected.
- Style-only artifact behavior should be tested/documented for run, report, and repair separately.
- Audit source-file rosters need a transitive-import or explicit contract test.

## 17. Proposed Future Work Order

1. **Document current compatibility contract in code/tests** without behavior change: list exact H08/H09 compared fields and state that source hashes are audit-only.
2. **Add failing compatibility tests** for a hypothetical actual/expected epoch, including old-component fallback and compatible-fingerprint allowlist behavior.
3. **Introduce the two semantics epochs/profiles** in a dedicated release; add them to components, reuse components, and explicit comparator logic.
4. **Define rollout behavior for v5.6 artifacts:** one-time fail-closed rebuild unless a golden-equivalence allowlist explicitly certifies the legacy profile.
5. **Surface source drift in the technical audit** and remove the currently unused warning variable/path ambiguity.
6. **Revalidate dynamic actual compatibility on session open/report** or make write-through-only ownership enforceable.
7. **Separate dynamic authoritative fields from provenance fields** to reduce note/timestamp invalidation.
8. **Evaluate canonical actual payload sealing** before considering any relaxation of whole-XLSX hashes.
9. **Review mutable decision DB integrity** as a separate event-log problem.
10. **Only then clean legacy/duplicate hashes** (H06/H07/H24/H34/H35), with schema migration and tests.

## 18. Explicit List of Things That Should NOT Be Changed

1. Do not remove or weaken any runtime asset SHA because the roster looks large.
2. Do not update a manifest SHA to match unexplained bytes; establish provenance first.
3. Do not recombine actual and expected assets into one cache fingerprint.
4. Do not make decoder/resolver raw source hashes the automatic invalidator; use an explicit semantic contract.
5. Do not put GUI, README, or other nonsemantic files into reuse keys.
6. Do not treat H18/H19 as cache keys or include actual/expected readings in their identity payloads.
7. Do not use Excel row number in normal occurrence identity.
8. Do not use CFF annotation H17 as the reusable CFF glyph-truth key; retain H14 full-glyph identity.
9. Do not drop conflict rows from dynamic actual dependencies.
10. Do not add provenance H22 to runtime reuse.
11. Do not relax H28/H29/H30 until an equally strong canonical authoritative-artifact model exists.
12. Do not treat final report or `pipeline_status.json` as authoritative sealed evidence.
13. Do not silently migrate identity schema; require an explicit adapter.
14. Do not introduce an epoch only in `reuse_components`; the semantic comparator must enforce it.
15. Do not carry an old expected-fingerprint allowlist across a semantics-profile mismatch by default.
16. Do not implement v5.7.0/v5.8/v5.9 behavior as part of this audit.

## Verification Record

- Initial working tree: clean.
- Branch / base: exact match to requested branch and commit.
- Static search: all tracked text files and all Python producers/consumers; 66 text files contained one or more broad search terms.
- Runtime asset validation: `ok=True`, schema `2.6.2`, 20 assets, 11 actual, 9 expected, 0 errors, 0 warnings.
- Existing unittest discovery: 155 discovered executions; 153 passed, 1 GUI/no-display test skipped, 1 module import error because the environment lacks `pytest`.
- The six functions in the affected locator module were then executed directly with an in-memory `pytest.raises` equivalent: 6/6 passed. No test file was changed.
- Production behavior/files: unchanged.
- Required final checks (`git diff --check`, sole-file diff, commit, and push) are recorded in the delivery commit rather than changing this architecture analysis.
