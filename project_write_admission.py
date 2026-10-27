"""Read-only admission for the existing project writer paths.

Admission is a byte receipt, never a repair operation or a permission to learn.
Legacy absence remains explicit; an existing project's missing manifest is not
reinterpreted as a request to create a new project.
"""
from __future__ import annotations

from contextvars import ContextVar
from contextlib import contextmanager
from dataclasses import dataclass
from functools import wraps
import inspect
import hashlib
from pathlib import Path

_ACTIVE = ContextVar('project_write_admission', default=())
_CREATING = ContextVar('project_creation_admission', default=())
_ENTRY_RECEIPTS = ContextVar('surface_entry_receipts', default=())


def entry_write_admission(project):
    project=Path(project).resolve()
    return next((receipt for owner,receipt in reversed(_ENTRY_RECEIPTS.get()) if owner==project),None)


@contextmanager
def _entry_receipt(receipt):
    if receipt is None:
        yield
        return
    token=_ENTRY_RECEIPTS.set((*_ENTRY_RECEIPTS.get(),(receipt.project,receipt)))
    try:
        yield
    finally:
        _ENTRY_RECEIPTS.reset(token)


@contextmanager
def owned_refresh_stage(stage, source, *, _phase=None):
    """Only the existing publisher may work in its newly allocated own stage."""
    stage, source = Path(stage).resolve(), Path(source).resolve()
    receipt = (_phase.receipt() if _phase is not None else
               capture_project_write_admission(source, recovery=True) if has_surface_indicators(source) else None)
    if receipt is not None:
        receipt.recheck()
    if (stage.parent != source.parent or not stage.name.startswith('.ar-')
            or not stage.is_dir() or any(stage.iterdir())):
        raise ValueError('PROJECT_WRITE_ADMISSION: invalid fresh refresh stage')
    token = _CREATING.set((*_CREATING.get(), stage))
    try:
        yield
    finally:
        _CREATING.reset(token)


def _bytes(path):
    path = Path(path)
    if path.is_symlink() or path.is_junction():
        raise ValueError('PROJECT_WRITE_ADMISSION: linked project evidence is unsupported')
    if not path.exists():
        return None
    if not path.is_file():
        raise ValueError('PROJECT_WRITE_ADMISSION: expected a regular evidence file: ' + str(path))
    return path.read_bytes()


@dataclass(frozen=True)
class ProjectWriteAdmission:
    project: Path
    files: tuple
    profile: bool = False

    def expected_bytes(self, path):
        name = str(Path(path).resolve())
        for captured, raw in self.files:
            if captured == name:
                return raw
        raise ValueError('PROJECT_WRITE_ADMISSION: file not in original receipt: ' + name)

    def recheck(self):
        for name, expected in self.files:
            if _bytes(Path(name)) != expected:
                raise ValueError('PROJECT_WRITE_ADMISSION: source changed before write: ' + name)


class SurfaceWritePhase:
    """In-memory continuation of one admitted surface lifecycle, not a loader.

    New values may only be outputs planned before our own write. A legal but
    different on-disk document never establishes a new continuation baseline.
    """
    def __init__(self, receipt):
        if type(receipt) is not ProjectWriteAdmission:
            raise ValueError('SURFACE_PHASE: original admission required')
        self.project=receipt.project
        self.profile=receipt.profile
        self.files=dict(receipt.files)
        self.pending=None
        self.attempted_paths=set()

    def _key(self,path):
        return str(Path(path).resolve())

    def expected_bytes(self,path):
        key=self._key(path)
        if key not in self.files:
            raise ValueError('SURFACE_PHASE: unbound path: '+key)
        return self.files[key]

    def receipt(self):
        return ProjectWriteAdmission(self.project,tuple(sorted(self.files.items())),self.profile)

    def recheck(self):
        self.receipt().recheck()

    def include(self,path,expected):
        key=self._key(path)
        if key in self.files and self.files[key]!=expected:
            raise ValueError('SURFACE_PHASE: conflicting known input: '+key)
        if _bytes(Path(key))!=expected:
            raise ValueError('SURFACE_PHASE: source changed before write: '+key)
        self.files[key]=expected

    def plan(self,outputs):
        if self.pending is not None:
            raise ValueError('SURFACE_PHASE: unfinished owned write')
        changes={self._key(path):raw for path,raw in outputs.items()}
        for name in changes:
            if name not in self.files:
                self.include(name,None)
        self.recheck()
        self.pending=({name:self.files[name] for name in changes},changes)

    def written(self):
        if self.pending is None:
            raise ValueError('SURFACE_PHASE: no planned output')
        self.files.update(self.pending[1])
        self.pending=None
        self.recheck()

    def attempting(self):
        if self.pending is None:
            raise ValueError('SURFACE_PHASE: no planned output')
        self.attempted_paths.update(self.pending[1])

    def reconcile(self):
        """An interrupted atomic write permits only its known before/after."""
        if self.pending is not None:
            before,after=self.pending
            actual={name:_bytes(Path(name)) for name in before}
            if actual==after:
                self.files.update(after)
            elif actual!=before:
                raise ValueError('SURFACE_PHASE: foreign bytes after interrupted write')
            self.pending=None
        self.recheck()


def project_from_actual_root(root):
    root = Path(root).resolve()
    if root.name == 'actual' and root.parent.name == '_專案證據':
        return root.parent.parent
    return None


def has_surface_indicators(project):
    """Finite format indicators; this never interprets absence as conversion."""
    import manual_surface_actual as surface
    project=Path(project).resolve()
    root=project/'_專案證據'/'actual'
    if _bytes(root/surface.FILE) is not None:
        return True
    publication_raw=_bytes(project/'actual_refresh_publication.json')
    if publication_raw is not None:
        publication=surface.parse(publication_raw)
        if not isinstance(publication,dict):
            raise ValueError('PROJECT_WRITE_ADMISSION: publication journal root invalid')
        if publication.get('schema') != 'actual-deferred-refresh/1':
            return True
        entries=publication.get('entries')
        if not isinstance(entries,list):
            raise ValueError('PROJECT_WRITE_ADMISSION: publication journal entries invalid')
        surface_path=str(Path('_專案證據')/'actual'/surface.FILE)
        if any(isinstance(entry,dict) and entry.get('path')==surface_path for entry in entries):
            return True
    raw=_bytes(project/'校對工作階段.json')
    if raw is not None:
        manifest=surface.parse(raw)
        if not isinstance(manifest,dict):
            raise ValueError('PROJECT_WRITE_ADMISSION: manifest root must be an object')
        if _bytes(project/'校對工作階段.json') != raw:
            raise ValueError('PROJECT_WRITE_ADMISSION: manifest changed during format detection; no later preimage adopted')
        if {surface.MARKER,'manual_surface_actual_projection','manual_surface_projection_refresh'} & manifest.keys():
            return True
    raw=_bytes(root/'global_exact_glyph_project_transaction.json')
    if raw is not None:
        import global_glyph_promotion as promotion
        return promotion._validated_project_journal(raw)['schema_version'] != '1.0'
    return False


def capture_project_write_admission(output_dir, *, allow_new=False, recovery=False):
    """Validate an existing project without mkdir, lock, journal or status writes."""
    import standalone_proofread as sp
    import actual_review as ar
    import global_glyph_promotion as promotion
    project = Path(output_dir).resolve()
    if project in _CREATING.get():
        return None
    if project in _ACTIVE.get():
        return None
    token = _ACTIVE.set((*_ACTIVE.get(), project))
    try:
        manifest_path = project/'校對工作階段.json'
        manifest_raw = _bytes(manifest_path)
        if manifest_raw is None:
            if allow_new and (not project.exists() or (project.is_dir() and not any(project.iterdir()))):
                return ProjectWriteAdmission(project, ((str(manifest_path), None),))
            raise ValueError('PROJECT_WRITE_ADMISSION: existing project manifest missing; no initialization performed')
        manifest = sp.json_parse_strict(manifest_raw, manifest_path)
        sp.validate_manifest_integrity(manifest)
        if (not sp.schema_compatible(manifest.get('session_schema_version'), sp.SESSION_SCHEMA_VERSION)
                or not sp.schema_compatible(manifest.get('ledger_schema_version'), sp.LEDGER_SCHEMA_VERSION)
                or not sp.schema_compatible(manifest.get('workbook_schema_version'), sp.WORKBOOK_SCHEMA_VERSION)
                or not sp.review_id_schema_compatible(manifest.get('review_id_schema_version'), sp.REVIEW_ID_SCHEMA_VERSION)):
            raise ValueError('PROJECT_WRITE_ADMISSION: unsupported project schema')
        root = sp.project_actual_evidence_root(project)
        captured = {manifest_path: manifest_raw}
        for name in ('待人工確認.json','pipeline_status.json'):
            captured[project/name]=_bytes(project/name)
        db_path = project/'人工判定資料庫.json'
        captured[db_path] = _bytes(db_path)
        # Missing DB and an empty object are legal historical states. Never run
        # the legacy orphan-and-reset writer during read-only admission.
        if captured[db_path] is not None:
            db = sp.json_parse_strict(captured[db_path], db_path)
            if not isinstance(db, dict):
                raise ValueError('DATA_INTEGRITY_ERROR：現版人工判定資料庫根節點必須是物件')
            current_db = (manifest.get('manual_surface_actual_contract') is not None or any(
                db.get(key) == value for key,value in [('version',sp.VERSION),
                    ('session_schema_version',sp.SESSION_SCHEMA_VERSION),('review_id_schema_version',sp.REVIEW_ID_SCHEMA_VERSION)]))
            if current_db:
                sp.normalize_db(db)
        elif manifest.get('manual_surface_actual_contract') is not None:
            raise ValueError('PROJECT_WRITE_ADMISSION: required new-format decision database missing')
        journal_path = root/promotion.PROJECT_TRANSACTION_FILE
        captured[journal_path] = _bytes(journal_path)
        journal = None
        if captured[journal_path] is not None:
            journal = promotion._validated_project_journal(captured[journal_path])
            if manifest.get('manual_surface_actual_contract') is not None and journal['schema_version'] != '2.0':
                raise ValueError('PROJECT_WRITE_ADMISSION: new surface format requires an owned journal/2')
            if journal['schema_version'] == '2.0':
                owner = journal['owner']
                if (owner['project'] != str(project) or owner['session_id'] != manifest['session_id']
                        or (journal['state']=='PREPARED' and owner['origin_manifest_sha256'] != manifest['manifest_integrity_sha256'])):
                    raise ValueError('PROJECT_WRITE_ADMISSION: foreign surface transaction owner')
                if owner['operation']=='surface-only':
                    promotion._surface_transaction_images(journal,manifest)
            if journal['state'] == 'PREPARED' and not recovery:
                raise ValueError('PROJECT_WRITE_ADMISSION: valid pending rollback requires explicit recovery')
        publication_path = project/sp.ACTUAL_REFRESH_PUBLICATION
        captured[publication_path] = _bytes(publication_path)
        publication = None
        if captured[publication_path] is not None:
            publication = sp._read_actual_refresh_publication(project)
            # Recovery's backup and acknowledgement bytes are part of the
            # first-write receipt too, not just the journal's JSON text.
            for index, entry in enumerate(publication[0]['entries']):
                for path in (publication[2]/'previous'/str(index),publication[2]/entry['path']):
                    captured[path]=_bytes(path)
            captured[publication[2]/'ack-committed.json']=_bytes(publication[2]/'ack-committed.json')
            if publication[0]['phase'] == 'PREPARED' and not recovery:
                raise ValueError('PROJECT_WRITE_ADMISSION: valid pending publication requires explicit recovery')
        # The original manifest and before-images define the legal recovery
        # view; do not repair damaged data to make ordinary admission succeed.
        preimages = {}
        if journal is not None and journal['state'] == 'PREPARED':
            preimages = {name: promotion._unpacked(raw) for name, raw in journal['files'].items()}
        specs = [(ar.OCCURRENCE_OVERRIDE_FILE, ar.OVERRIDE_HEADERS),
                 (ar.USER_GLYF_FILE, ar.USER_GLYF_HEADERS), (ar.USER_CFF_FILE, ar.USER_CFF_HEADERS),
                 (ar.GLYPH_CONFLICT_FILE, ar.GLYPH_CONFLICT_HEADERS),
                 (ar.GLYPH_PROVENANCE_FILE, ar.GLYPH_PROVENANCE_HEADERS)]
        legacy = manifest.get('manual_surface_actual_contract') is None
        for name, headers in specs:
            path = root/name
            captured[path] = _bytes(path)
            if (manifest.get('manual_surface_actual_contract') is not None
                    and captured[path] is None):
                raise ValueError('PROJECT_WRITE_ADMISSION: missing new-format evidence is not a recovery instruction')
            raw = preimages[name] if name in preimages else captured[path]
            if raw is None:
                if name != ar.OCCURRENCE_OVERRIDE_FILE and not legacy:
                    raise ValueError('PROJECT_WRITE_ADMISSION: required actual evidence missing: ' + name)
                continue
            if not legacy:
                ar._validated_dynamic_csv_rows(raw, name, headers)
                if captured[path] is not None and captured[path] != raw:
                    ar._validated_dynamic_csv_rows(captured[path], name, headers)
        if not legacy and not (journal and journal['state'] == 'PREPARED'):
            report = ar.validate_dynamic_actual_evidence(root, read_only=True)
            if not report['ok']:
                raise ValueError('PROJECT_WRITE_ADMISSION: corrupt dynamic actual evidence: ' + str(report['errors']))
        staging_path = root/ar.MANUAL_ACTUAL_STAGING_FILE
        captured[staging_path] = _bytes(staging_path)
        if not legacy and not (journal and journal['state'] == 'PREPARED'):
            ar.load_manual_actual_staging(root)
        outbox_path = root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        captured[outbox_path] = _bytes(outbox_path)
        if not legacy and not (journal and journal['state'] == 'PREPARED'):
            promotion.load_promotion_outbox(root)
        import manual_surface_actual as surface
        surface_path = root/surface.FILE
        captured[surface_path] = _bytes(surface_path)
        if surface.enabled(manifest) and captured[surface_path] is None:
            raise ValueError('PROJECT_WRITE_ADMISSION: missing required surface proof is not a recovery instruction')
        surface_raw = preimages.get(surface.FILE, captured[surface_path])
        if surface.enabled(manifest):
            current_document = surface.validate_document(surface.parse(captured[surface_path]), session_id=manifest['session_id'])
            surface.verify_sources(current_document, manifest, project)
            if surface_raw is None:
                raise ValueError('PROJECT_WRITE_ADMISSION: required surface evidence missing')
            document = current_document
            if surface_raw != captured[surface_path]:
                document = surface.validate_document(surface.parse(surface_raw), session_id=manifest['session_id'])
                surface.verify_sources(document, manifest, project)
            if journal is not None and journal['schema_version']=='2.0' and journal['state']=='COMMITTED' and journal['owner']['operation']=='surface-only':
                claims={item['proof']['source']['occurrence_id']:item['reading'] for item in current_document['records']}
                if any(claims.get(item['occurrence_id']) != item['reading']
                        for item in journal['recovery_plan']['checked_postconditions']):
                    raise ValueError('PROJECT_WRITE_ADMISSION: committed surface plan differs from required receipts')
            projected = manifest.get('manual_surface_actual_projection')
            if (projected != surface.projection(document)
                    and journal is None and publication is None):
                raise ValueError('PROJECT_WRITE_ADMISSION: applied surface evidence differs from sealed projection')
        elif surface_raw is not None and publication is None:
            raise ValueError('PROJECT_WRITE_ADMISSION: orphan surface format; no automatic conversion')
        if (journal is not None and journal['schema_version']=='2.0'
                and journal['owner']['operation']=='surface-only' and journal['state']=='PREPARED'):
            promotion._surface_rollback_preconditions(journal,manifest,
                {name:captured[root/name] for name in journal['files']},captured[staging_path])
        # Reuse the same finite source/artifact binding implementation as saves.
        from review_save_service import ReviewSaveService
        binder = ReviewSaveService(project)
        try:
            binder._specs(manifest)
        except ValueError as exc:
            raise ValueError('SOURCE_INVALID／DATA_INTEGRITY_ERROR：檔案內容已變更或來源定位無法證明：'+str(exc)) from exc
        runtime = binder._runtime({}) if surface.enabled(manifest) else []
        for path, expected in [*binder._source_specs.items(), *binder._artifact_specs.items(), *runtime]:
            raw = _bytes(path)
            if raw is None or hashlib.sha256(raw).hexdigest() != expected:
                if publication is None:
                    raise ValueError('SOURCE_INVALID／DATA_INTEGRITY_ERROR：檔案內容已變更：' + str(path))
                # Publication's reader already checks every current file against
                # its legal old/new bytes. Only an owned entry may differ.
                if not Path(path).is_relative_to(project):
                    raise ValueError('PROJECT_WRITE_ADMISSION: external source changed during recovery')
                relative = str(Path(path).relative_to(project))
                allowed = {entry['path'] for entry in publication[0]['entries']}
                if relative not in allowed:
                    raise ValueError('PROJECT_WRITE_ADMISSION: unrelated source changed during recovery')
            captured[Path(path)] = raw
        receipt = ProjectWriteAdmission(project, tuple(sorted((str(path), raw) for path, raw in captured.items())),
                                        profile=surface.enabled(manifest))
        receipt.recheck()
        return receipt
    finally:
        _ACTIVE.reset(token)


def project_write_entry(*, allow_new=False, recovery=False, locked=False, surface_conversion=False, profile_only=False):
    """Put existing-project admission before an affected writer's first side effect."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def run(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            project = Path(arguments['output_dir']).resolve()
            if profile_only and not has_surface_indicators(project):
                # The existing local-transaction source-context contract is
                # not a full project schema. Its original reader stays owner.
                return function(*args, **kwargs)
            receipt = capture_project_write_admission(project, allow_new=allow_new, recovery=recovery)
            if receipt is not None:
                receipt.recheck()
            if surface_conversion:
                # Explicit opt-in is permitted only from a complete existing
                # source. Refuse incomplete legacy data before creating a lock.
                import manual_surface_actual as surface
                import standalone_proofread as sp
                manifest = sp.json_load_strict(project/'校對工作階段.json')
                if not surface.enabled(manifest):
                    surface.raw_dependencies(sp.project_actual_evidence_root(project))
                    sp.normalize_db(sp.json_load_strict(project/'人工判定資料庫.json'))
                    import actual_review as ar
                    report=ar.validate_dynamic_actual_evidence(sp.project_actual_evidence_root(project),read_only=True)
                    if not report['ok']:
                        raise ValueError('SURFACE_CONTRACT: conversion source invalid: '+str(report['errors']))
                    ar.load_manual_actual_staging(sp.project_actual_evidence_root(project))
                    import global_glyph_promotion as promotion
                    promotion.load_promotion_outbox(sp.project_actual_evidence_root(project))
            creating = receipt is not None and dict(receipt.files).get(str(project/'校對工作階段.json')) is None
            if creating:
                if project.exists() and (not project.is_dir() or any(project.iterdir())):
                    raise ValueError('PROJECT_WRITE_ADMISSION: new output changed before creation')
                token = _CREATING.set((*_CREATING.get(), project))
                try:
                    return function(*args, **kwargs)
                finally:
                    _CREATING.reset(token)
            if locked:
                from global_glyph_promotion import project_delivery_lock
                with project_delivery_lock(project/'_專案證據'/'actual',
                        **({'_admission':receipt} if receipt is not None and receipt.profile else {})):
                    if receipt is not None:
                        receipt.recheck()
                    with _entry_receipt(receipt):
                        return function(*args, **kwargs)
            with _entry_receipt(receipt):
                return function(*args, **kwargs)
        return run
    return decorate
