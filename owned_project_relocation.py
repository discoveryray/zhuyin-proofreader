"""Finite in-memory relocation of owned actual transactions; no journal migration.

Historical paths are descriptors only. The selected sealed project and the
existing publisher's beforeimages supply authority; never open the old root.
"""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from sealed_workbook_paths import trusted_root


def _fail(message):
    raise ValueError('OWNED_RELOCATION: ' + message)


def _historical_path(value):
    if (not isinstance(value, str) or not Path(value).is_absolute()
            or '..' in Path(value).parts or str(Path(value)) != value):
        _fail('noncanonical historical locator')
    return Path(value)


def _safe_path(path):
    path = Path(path).absolute()
    if any(part.is_symlink() or part.is_junction() for part in (path, *path.parents)):
        _fail('linked dependency')
    if path.exists() and (not path.is_file() or path.stat().st_nlink != 1):
        _fail('dependency is not an independent regular file')
    return path


def _native_roster(manifest):
    fields = ('occurrence_id', 'review_id', 'pdf_name', 'pdf_sha256', 'physical_page',
              'char', 'font_xref', 'glyph_id', 'zhuyin_component_id', 'x0', 'y0', 'x1', 'y1')
    result = {}
    for row in manifest['records']:
        oid = row['occurrence_id']
        if oid in result:
            _fail('duplicate native occurrence')
        result[oid] = tuple(row.get(key) for key in fields)
    return result


def _pdf_roster(manifest):
    result = {}
    for info in manifest['pdfs']:
        name, digest = info['pdf_name'], info['pdf_sha256']
        if name in result:
            _fail('duplicate sealed PDF')
        result[name] = digest
    for row in manifest['records']:
        if result.get(row['pdf_name']) != row['pdf_sha256']:
            _fail('native occurrence is outside complete PDF roster')
    return result


@dataclass(frozen=True)
class OwnedProjectBinding:
    project: Path
    origin: Path
    owner: tuple
    transaction_id: str
    plan_sha: str
    origin_manifest: bytes
    dependencies: tuple
    paths: tuple
    directory_identity: tuple
    publication_owner: str | None = None

    def recheck(self):
        trusted_root(self.project)
        stat = self.project.stat()
        if (stat.st_dev, stat.st_ino) != self.directory_identity:
            _fail('selected project directory changed')
        for path in self.paths:
            _safe_path(path)

    def check_owner(self, owner, manifest, *, origin_manifest=None):
        import standalone_proofread as sp
        if tuple(sorted(owner.items())) != self.owner:
            _fail('transaction owner differs from original binding')
        original = json.loads(self.origin_manifest)
        if origin_manifest is not None and origin_manifest != original:
            _fail('publisher origin changed')
        sp.validate_manifest_integrity(manifest)
        if (manifest['session_id'] != owner['session_id']
                or _pdf_roster(manifest) != _pdf_roster(original)
                or _native_roster(manifest) != _native_roster(original)):
            _fail('selected session/PDF/native identity differs')
        self.recheck()

    def check_publication(self, publication, old_manifest):
        owner = dict(self.owner)
        if (publication['project'] not in {str(self.origin), str(self.project), self.publication_owner}
                or publication['session_id'] != owner['session_id']
                or publication['token'] != self.transaction_id
                or publication['plan_sha'] != self.plan_sha
                or old_manifest != json.loads(self.origin_manifest)):
            _fail('publication does not retain original owner/manifest/token/plan')
        self.recheck()

    def dependency_map(self, original):
        mapping = dict(self.dependencies)
        if set(mapping) != set(original):
            _fail('dependency roster differs from bound original')
        result = {mapping[name]: digest for name, digest in original.items()}
        if len(result) != len(original):
            _fail('dependency collision')
        return result


def capture_owned_binding(project, journal, manifest, *, origin_manifest=None,
                          sources=None, runtime=(), dependency_names=None, publication=None):
    """Capture only validated journal/2 or /3; caller still proves all file bytes."""
    import standalone_proofread as sp
    import global_glyph_promotion as promotion
    project = trusted_root(project)
    journal = promotion._validated_project_journal(json.dumps(journal).encode('utf-8'))
    if journal['schema_version'] not in {'2.0', '3.0'}:
        _fail('unsupported owned journal')
    owner = journal['owner']
    origin = _historical_path(owner['project'])
    original = manifest if origin_manifest is None else origin_manifest
    sp.validate_manifest_integrity(original)
    if (original['session_id'] != owner['session_id']
            or original['manifest_integrity_sha256'] != owner['origin_manifest_sha256']):
        _fail('original owner/session/manifest seal differs')
    # Workbook role/name remains fixed across ordinary refresh; only SHA may
    # advance through the original publisher's individually checked entries.
    from sealed_workbook_paths import descriptor
    if ([(info['pdf_name'], kind, descriptor(info, kind)) for info in original['pdfs'] for kind in ('actual','candidate')]
            != [(info['pdf_name'], kind, descriptor(info, kind)) for info in manifest['pdfs'] for kind in ('actual','candidate')]):
        _fail('workbook role/relative roster differs')
    mappings = {}
    if dependency_names is not None and journal['schema_version'] == '3.0':
        allowed = set(dependency_names)
        runtime_names = {str(path) for path, _ in runtime}
        source_names = {str(path): digest for path, digest in (sources or {}).items()}
        # The finite historical PDF locators are the same named adjacent,
        # selected-root and sealed producer locators used by source binding.
        historical_sources = {}
        for info in [*original['pdfs'], *original['records']]:
            wanted = info['pdf_sha256']
            candidates = {origin.parent / info['pdf_name'], origin / info['pdf_name']}
            for value in (info.get('pdf'), (info.get('source_record') or {}).get('pdf')):
                if value and Path(value).is_absolute():
                    candidates.add(_historical_path(value))
            # Reuse the source's own sealed locator, not a hash-only choice
            # among different PDF names with identical content.
            located = _safe_path(sp._resolve_pdf_path(info, project))
            target = str(located.resolve())
            if source_names.get(target) != wanted:
                _fail('sealed external PDF binding missing or ambiguous')
            for candidate in candidates:
                key = str(candidate)
                if key in historical_sources and historical_sources[key] != target:
                    _fail('historical source locator collision')
                historical_sources[key] = target
        for name in journal['write_plan']['dependencies']:
            path = _historical_path(name)
            if path.is_relative_to(origin):
                mapped = str(project / path.relative_to(origin))
            elif name in runtime_names:
                mapped = name
            elif name in historical_sources:
                mapped = historical_sources[name]
            else:
                _fail('unknown external dependency')
            if mapped not in allowed or mapped in mappings.values():
                _fail('missing/colliding dependency')
            mappings[name] = mapped
    stat = project.stat()
    binding = OwnedProjectBinding(project, origin, tuple(sorted(owner.items())), journal['transaction_id'],
        sp._hash_review_payload(journal['recovery_plan']),
        json.dumps(original, sort_keys=True, ensure_ascii=False).encode('utf-8'),
        tuple(sorted(mappings.items())), tuple(dependency_names or ()), (stat.st_dev, stat.st_ino),
        str(_historical_path(publication['project'])) if publication is not None else None)
    binding.check_owner(owner, manifest)
    if publication is not None:
        binding.check_publication(publication, original)
    return binding


def binding_for_publication(project, publication, old_manifest):
    """Read-only bootstrap; local journal or the exact ACK receipt is required."""
    import standalone_proofread as sp
    import global_glyph_promotion as promotion
    project = trusted_root(project)
    path = _safe_path(sp.project_actual_evidence_root(project) / promotion.PROJECT_TRANSACTION_FILE)
    if not path.exists() and publication['phase'] == 'ACKNOWLEDGING':
        path = _safe_path(project.parent / publication['stage'] / 'ack-committed.json')
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != publication['ack_sha']:
            _fail('acknowledgement authority missing')
    if not path.is_file():
        _fail('moved publication lacks owned transaction authority')
    journal = promotion._validated_project_journal(path.read_bytes())
    manifest = sp.json_load_strict(project / '校對工作階段.json')
    binding = capture_owned_binding(project, journal, manifest, origin_manifest=old_manifest, publication=publication)
    binding.check_publication(publication, old_manifest)
    return binding
