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
_ENTRY_PHASES = ContextVar('profile_entry_phases', default=())
_TRANSFER_CREATION = ContextVar('owned_transfer_creation', default=None)


def entry_write_admission(project):
    project=Path(project).resolve()
    return next((receipt for owner,receipt in reversed(_ENTRY_RECEIPTS.get()) if owner==project),None)


def entry_write_phase(project):
    project=Path(project).resolve()
    return next((phase for owner,phase in reversed(_ENTRY_PHASES.get()) if owner==project),None)


@contextmanager
def _entry_receipt(receipt):
    if receipt is None:
        yield
        return
    token=_ENTRY_RECEIPTS.set((*_ENTRY_RECEIPTS.get(),(receipt.project,receipt)))
    existing=entry_write_phase(receipt.project)
    phase_token=None
    if receipt.profile and existing is None:
        phase_token=_ENTRY_PHASES.set((*_ENTRY_PHASES.get(),(receipt.project,SurfaceWritePhase(receipt))))
    try:
        yield
    finally:
        if phase_token is not None:_ENTRY_PHASES.reset(phase_token)
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


@dataclass(frozen=True)
class _FreshTransferTarget:
    project: Path
    directory_identity: tuple | None


def capture_fresh_transfer_target(project):
    """Read-only origin, before the transfer lock creates its own prefix."""
    project=Path(project).resolve()
    if project.is_symlink() or project.is_junction():
        raise ValueError('PROJECT_WRITE_ADMISSION: linked transfer target')
    if project.exists():
        if not project.is_dir() or any(project.iterdir()):
            raise ValueError('接續目標資料夾必須為空，避免覆寫既有成果')
        stat=project.stat()
        identity=(stat.st_dev,stat.st_ino)
    else:
        identity=None
    return _FreshTransferTarget(project,identity)


class _TransferCreation:
    def __init__(self, origin, marker_sha, source_guard):
        if type(origin) is not _FreshTransferTarget:
            raise ValueError('PROJECT_WRITE_ADMISSION: original empty transfer target required')
        self.origin=origin
        self.marker_sha=marker_sha
        self.source_guard=source_guard
        self.claimed=False
        self.started=False

    def recheck(self):
        from global_glyph_promotion import _ACTIVE_LOCKS
        from pdf_portability import INCOMPLETE_FILE
        project=self.origin.project
        actual=project/'_專案證據'/'actual'
        if not _ACTIVE_LOCKS.get(str(actual)):
            raise ValueError('PROJECT_WRITE_ADMISSION: owned transfer lock required')
        if self.origin.directory_identity is not None:
            stat=project.stat()
            if (stat.st_dev,stat.st_ino)!=self.origin.directory_identity:
                raise ValueError('PROJECT_WRITE_ADMISSION: transfer target directory changed')
        marker=project/INCOMPLETE_FILE
        expected={project/'_專案證據',actual,actual/'.global_exact_glyph_delivery.lock',marker}
        paths=set(project.rglob('*'))
        if paths!=expected or any(path.is_symlink() or path.is_junction() for path in paths):
            raise ValueError('PROJECT_WRITE_ADMISSION: fresh transfer namespace changed before write')
        raw=_bytes(marker)
        if raw is None or hashlib.sha256(raw).hexdigest()!=self.marker_sha:
            raise ValueError('PROJECT_WRITE_ADMISSION: transfer marker changed before write')
        self.source_guard()


@contextmanager
def owned_transfer_creation(origin, marker_sha, source_guard):
    """Continue only this empty-origin transfer's lock + own marker prefix.

    The marker alone grants nothing. This capability is private, single-use,
    under the existing transfer lock, and checked again at the first producer
    side effect. Subsequent outputs belong to the existing producer contract.
    """
    capability=_TransferCreation(origin,marker_sha,source_guard)
    capability.recheck()
    token=_TRANSFER_CREATION.set(capability)
    try:
        yield
    finally:
        _TRANSFER_CREATION.reset(token)


def begin_owned_transfer_write(project):
    capability=_TRANSFER_CREATION.get()
    if capability is not None and Path(project).resolve()==capability.origin.project:
        if not capability.claimed:
            raise ValueError('PROJECT_WRITE_ADMISSION: transfer producer not admitted')
        if not capability.started:
            capability.recheck()
            capability.started=True


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
    write_block: str | None = None
    workbook_roster: tuple | None = None
    integrity_paths: tuple = ()
    relocation: object = None
    retired_relocations: tuple = ()

    def expected_bytes(self, path):
        name = str(Path(path).resolve())
        for captured, raw in self.files:
            if captured == name:
                return raw
        raise ValueError('PROJECT_WRITE_ADMISSION: file not in original receipt: ' + name)

    def recheck(self):
        if self.relocation is not None:
            self.relocation.recheck()
        for binding in self.retired_relocations:
            binding.recheck()
        if self.workbook_roster is not None:
            from sealed_workbook_paths import workbook_role_paths
            if tuple(str(path) for path in workbook_role_paths(self.project))!=self.workbook_roster:
                raise ValueError('PROJECT_WRITE_ADMISSION: workbook role namespace changed before write')
        for name, expected in self.files:
            if _bytes(Path(name)) != expected:
                raise ValueError(('SOURCE_INVALID／DATA_INTEGRITY_ERROR: ' if name in self.integrity_paths else '')
                    + 'PROJECT_WRITE_ADMISSION: source changed before write: ' + name)

    def require_writable(self):
        if self.write_block:
            raise ValueError('PROJECT_WRITE_ADMISSION: '+self.write_block)
        self.recheck()


class SurfaceWritePhase:
    """In-memory continuation of one admitted surface lifecycle, not a loader.

    New values may only be outputs planned before our own write. A legal but
    different on-disk document never establishes a new continuation baseline.
    """
    def __init__(self, receipt):
        if type(receipt) is not ProjectWriteAdmission:
            raise ValueError('SURFACE_PHASE: original admission required')
        receipt.require_writable()
        self.project=receipt.project
        self.profile=receipt.profile
        self.workbook_roster=receipt.workbook_roster
        self.integrity_paths=receipt.integrity_paths
        self.relocation=receipt.relocation
        self.retired_relocations=receipt.retired_relocations
        self.files=dict(receipt.files)
        self.pending=None
        self.attempted_paths=set()
        self._completed_write=None

    def _key(self,path):
        return str(Path(path).resolve())

    def expected_bytes(self,path):
        key=self._key(path)
        if key not in self.files:
            raise ValueError('SURFACE_PHASE: unbound path: '+key)
        return self.files[key]

    def receipt(self):
        return ProjectWriteAdmission(self.project,tuple(sorted(self.files.items())),self.profile,
            workbook_roster=self.workbook_roster,integrity_paths=self.integrity_paths,relocation=self.relocation,
            retired_relocations=self.retired_relocations)

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
        self._completed_write=self.pending
        self.files.update(self.pending[1])
        self.pending=None
        self.recheck()

    def complete_owned_rollback(self, original_journal_raw):
        """Retire one proven PREPARED authority, retaining every source guard.

        Only the recovery writer's exact journal unlink can cross this boundary.
        This never recaptures current files as a new baseline. The next ordinary
        transaction uses the same admitted bytes and selected project lock.
        """
        if self.relocation is None:
            return  # A fresh, same-entry transaction had no relocated authority.
        import global_glyph_promotion as promotion
        import standalone_proofread as sp
        from owned_project_relocation import recovery_plan_digest
        root=sp.project_actual_evidence_root(self.project)
        journal_path=root/promotion.PROJECT_TRANSACTION_FILE
        key=self._key(journal_path)
        journal=promotion._validated_project_journal(original_journal_raw)
        if (not promotion._ACTIVE_LOCKS.get(str(root)) or self.pending is not None
                or journal['schema_version'] not in {'2.0','3.0'} or journal['state']!='PREPARED'
                or journal['transaction_id']!=self.relocation.transaction_id
                or recovery_plan_digest(journal)!=self.relocation.plan_sha
                or self._completed_write!=({key:original_journal_raw},{key:None})
                or self.expected_bytes(journal_path) is not None
                or self.expected_bytes(self.project/sp.ACTUAL_REFRESH_PUBLICATION) is not None):
            raise ValueError('SURFACE_PHASE: owned rollback authority is not completely consumed')
        manifest=sp.json_parse_strict(self.expected_bytes(self.project/'校對工作階段.json'),
                                      self.project/'校對工作階段.json')
        self.relocation.check_owner(journal['owner'],manifest)
        if journal['owner']['origin_manifest_sha256']!=manifest['manifest_integrity_sha256']:
            raise ValueError('SURFACE_PHASE: rollback origin manifest changed')
        if (any(self.expected_bytes(root/name)!=promotion._unpacked(raw)
                for name,raw in journal['files'].items())
                or self.expected_bytes(root/promotion.STAGING_FILE)!=promotion._unpacked(journal['staging'])):
            raise ValueError('SURFACE_PHASE: rollback beforeimages are not fully restored')
        self.recheck()  # Includes any foreign write between unlink and handoff.
        # The old token/owner no longer authorizes a transaction, but its
        # selected-directory identity and no-link guards remain in force.
        self.retired_relocations=(*self.retired_relocations,self.relocation)
        self.relocation=None
        self._completed_write=None
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


def capture_project_write_admission(output_dir, *, allow_new=False, recovery=False, surface_conversion=False):
    """Validate an existing project without mkdir, lock, journal or status writes."""
    import standalone_proofread as sp
    import actual_review as ar
    import global_glyph_promotion as promotion
    from sealed_workbook_paths import trusted_root
    project = trusted_root(output_dir)
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
        import manual_surface_actual as surface
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
            if current_db or surface_conversion:
                sp.normalize_db(db)
        elif manifest.get('manual_surface_actual_contract') is not None:
            raise ValueError('PROJECT_WRITE_ADMISSION: required new-format decision database missing')
        elif surface_conversion:
            raise ValueError('SURFACE_CONTRACT: conversion decision database missing')
        journal_path = root/promotion.PROJECT_TRANSACTION_FILE
        captured[journal_path] = _bytes(journal_path)
        journal = None
        if captured[journal_path] is not None:
            journal = promotion._validated_project_journal(captured[journal_path])
            if manifest.get('manual_surface_actual_contract') is not None and journal['schema_version'] not in {'2.0','3.0'}:
                raise ValueError('PROJECT_WRITE_ADMISSION: new surface format requires an owned journal/2')
            if journal['schema_version'] in {'2.0','3.0'}:
                owner = journal['owner']
                if (owner['session_id'] != manifest['session_id']
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
        surface_entry=(surface.enabled(manifest) or surface_conversion or
            (publication is not None and publication[0]['schema']=='actual-deferred-refresh/2'))
        if surface_entry:
            from pdf_portability import ACTUAL_EXCEL_CONFLICT_FILE,CONFLICT_FILE,INCOMPLETE_FILE
            for name in (ACTUAL_EXCEL_CONFLICT_FILE,CONFLICT_FILE,INCOMPLETE_FILE,'注音校對_技術稽核.xlsx','注音校對_最終報告.xlsx'):
                captured[project/name]=_bytes(project/name)
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
        strict_actual=not legacy or surface_conversion
        for name, headers in specs:
            path = root/name
            captured[path] = _bytes(path)
            if (manifest.get('manual_surface_actual_contract') is not None
                    and captured[path] is None):
                raise ValueError('PROJECT_WRITE_ADMISSION: missing new-format evidence is not a recovery instruction')
            raw = preimages[name] if name in preimages else captured[path]
            if raw is None:
                if surface_conversion:
                    raise ValueError('SURFACE_CONTRACT: conversion raw evidence missing: '+name)
                if name != ar.OCCURRENCE_OVERRIDE_FILE and not legacy:
                    raise ValueError('PROJECT_WRITE_ADMISSION: required actual evidence missing: ' + name)
                continue
            if strict_actual:
                ar._validated_dynamic_csv_rows(raw, name, headers)
                if captured[path] is not None and captured[path] != raw:
                    ar._validated_dynamic_csv_rows(captured[path], name, headers)
        if not legacy and not (journal and journal['state'] == 'PREPARED'):
            report = ar.validate_dynamic_actual_evidence(root, read_only=True)
            if not report['ok']:
                raise ValueError('PROJECT_WRITE_ADMISSION: corrupt dynamic actual evidence: ' + str(report['errors']))
        staging_path = root/ar.MANUAL_ACTUAL_STAGING_FILE
        captured[staging_path] = _bytes(staging_path)
        if strict_actual and not (journal and journal['state'] == 'PREPARED'):
            raw=captured[staging_path]
            if raw is not None:
                ar._validate_manual_actual_staging_document(sp.json_parse_strict(raw,staging_path))
        outbox_path = root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE
        captured[outbox_path] = _bytes(outbox_path)
        if strict_actual and not (journal and journal['state'] == 'PREPARED'):
            raw=captured[outbox_path]
            if raw is not None:
                promotion.validate_outbox(promotion._json(raw))
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
        artifact_reader=None
        workbook_roster=None
        if surface_entry:
            from sealed_workbook_paths import workbook_role_paths
            paths=workbook_role_paths(project)
            workbook_roster=tuple(str(path) for path in paths)
            role_inputs={path:_bytes(path) for path in paths}
            captured.update(role_inputs)
            originals={}
            if publication is not None and publication[0]['schema']=='actual-deferred-refresh/2':
                from sealed_workbook_paths import descriptor
                wanted={project/descriptor(info,kind):info[kind+'_workbook_sha256']
                        for info in manifest['pdfs'] for kind in ('actual','candidate')}
                for entry in publication[0]['entries']:
                    path=project/entry['path']
                    if path in role_inputs:
                        old=publication[3].get(entry['path'])
                        if old is None or hashlib.sha256(old).hexdigest()!=entry['old_sha']:
                            raise ValueError('PROJECT_WRITE_ADMISSION: publication workbook backup buffer differs')
                        if wanted.get(path)==entry['old_sha']:originals[path]=old
            def artifact_reader(path):
                path=Path(path)
                if path not in role_inputs:
                    raise ValueError('PROJECT_WRITE_ADMISSION: workbook is outside original role-input roster')
                return originals.get(path,role_inputs[path])
        try:
            binder._specs(manifest,**({'_artifact_reader':artifact_reader} if artifact_reader is not None else {}))
        except ValueError as exc:
            raise ValueError('SOURCE_INVALID／DATA_INTEGRITY_ERROR：檔案內容已變更或來源定位無法證明：'+str(exc)) from exc
        runtime = binder._runtime({}) if surface_entry else []
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
        write_block=None
        if journal is not None and journal['schema_version']=='3.0' and not recovery:
            write_block='finish pending ordinary journal/3 recovery before another project write'
        if journal is not None and journal['schema_version']=='2.0' and surface.enabled(manifest) and journal['owner']['operation']=='direct-visual':
            unchanged=(all(captured[root/name]==promotion._unpacked(value) for name,value in journal['files'].items())
                       and captured[staging_path]==promotion._unpacked(journal['staging']))
            if journal['state']!='PREPARED' or not unchanged:
                write_block='legacy new-profile journal/2 direct pending lacks exact afterimages; read-only, no automatic upgrade/recovery'
        authority=journal
        if authority is None and publication is not None and publication[0]['phase']=='ACKNOWLEDGING':
            raw=captured.get(publication[2]/'ack-committed.json')
            if raw is not None:authority=promotion._validated_project_journal(raw)
        relocation=None
        if authority is not None and authority['schema_version'] in {'2.0','3.0'}:
            from owned_project_relocation import capture_owned_binding
            original=(sp.json_parse_strict(publication[3]['校對工作階段.json'],publication_path)
                      if publication is not None else manifest)
            relocation=capture_owned_binding(project,authority,manifest,origin_manifest=original,
                sources=binder._source_specs,runtime=runtime,
                dependency_names=tuple(str(path) for path in captured),
                publication=publication[0] if publication is not None else None)
            if publication is not None:relocation.check_publication(publication[0],original)
        receipt = ProjectWriteAdmission(project, tuple(sorted((str(path), raw) for path, raw in captured.items())),
                                        profile=surface.enabled(manifest),write_block=write_block,
                                        workbook_roster=workbook_roster,
                                        relocation=relocation,
                                        integrity_paths=tuple(str(Path(path)) for path in
                                            [*binder._source_specs,*binder._artifact_specs,*(path for path,_ in runtime)]))
        if surface_entry:
            from pdf_portability import actual_excel_conflict_state,validate_conflict_state,ACTUAL_EXCEL_CONFLICT_FILE,CONFLICT_FILE
            checked_db=sp.normalize_db(sp.json_parse_strict(captured[db_path],db_path))
            if captured[project/ACTUAL_EXCEL_CONFLICT_FILE] is not None:
                actual_excel_conflict_state(project,manifest,checked_db,_read_bytes=receipt.expected_bytes)
            if captured[project/CONFLICT_FILE] is not None:
                validate_conflict_state(project,manifest,checked_db,_read_bytes=receipt.expected_bytes)
        if authority is not None and authority['schema_version']=='3.0':
            promotion._check_direct_receipt(root,authority,receipt,publication)
        receipt.recheck()
        return receipt
    finally:
        _ACTIVE.reset(token)


def project_write_entry(*, allow_new=False, recovery=False, locked=False, surface_conversion=False, profile_only=False, owned_surface_recovery=False):
    """Put existing-project admission before an affected writer's first side effect."""
    def decorate(function):
        signature = inspect.signature(function)
        @wraps(function)
        def run(*args, **kwargs):
            arguments = signature.bind(*args, **kwargs).arguments
            project = Path(arguments['output_dir']).resolve()
            capability=_TRANSFER_CREATION.get()
            if allow_new and capability is not None and capability.origin.project==project:
                if capability.claimed:
                    raise ValueError('PROJECT_WRITE_ADMISSION: transfer producer already claimed')
                capability.recheck()
                capability.claimed=True
                token=_CREATING.set((*_CREATING.get(),project))
                try:
                    return function(*args,**kwargs)
                finally:
                    _CREATING.reset(token)
            if profile_only and not has_surface_indicators(project):
                # The existing local-transaction source-context contract is
                # not a full project schema. Its original reader stays owner.
                return function(*args, **kwargs)
            receipt = capture_project_write_admission(project, allow_new=allow_new, recovery=recovery,
                **({'surface_conversion':True} if surface_conversion else {}))
            if receipt is not None:
                receipt.require_writable()
                if (receipt.workbook_roster is not None and not receipt.profile
                        and not surface_conversion and not owned_surface_recovery):
                    raise ValueError('PROJECT_WRITE_ADMISSION: finish transitional surface publication via recover_committed_actual_project before ordinary write')
            # Conversion completeness and validators consumed the original
            # captured bytes above, before any project lock or nested service.
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
                        **({'_admission':receipt} if receipt is not None and receipt.workbook_roster is not None else {})):
                    if receipt is not None:
                        receipt.recheck()
                    with _entry_receipt(receipt):
                        return function(*args, **kwargs)
            with _entry_receipt(receipt):
                return function(*args, **kwargs)
        return run
    return decorate
