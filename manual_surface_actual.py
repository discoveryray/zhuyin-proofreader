"""Occurrence-local manual final-surface evidence, independent of glyph truth.

Only writers implementing this exact contract are supported. A support policy
does not prevent an unchanged old program from opening or writing the folder.
"""
from __future__ import annotations

import base64
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

CONTRACT = 'manual-surface-actual/1'
PROJECTION = 'actual-surface-projection/1'
FILE = 'manual_surface_actual.json'
MARKER = 'manual_surface_actual_contract'
PROJECT_MARKER = {'schema': CONTRACT, 'projection': PROJECTION,
                  'supported_writers': 'writers-validated-for-manual-surface-actual/1'}
UNSUPPORTED_WARNING = '此專案已啟用 manual-surface-actual/1；舊版不支援開啟或回寫。支援政策不會技術阻止舊程式直接寫入。'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _fields(value, keys, name):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ValueError('SURFACE_CONTRACT: ' + name + ' has missing/unknown fields')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError('SURFACE_CONTRACT: invalid ' + name)
    return value


def _sha(value, name):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('SURFACE_CONTRACT: invalid ' + name)


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('SURFACE_CONTRACT: duplicate key')
        out[key] = value
    return out


def parse(raw):
    return json.loads(raw.decode('utf-8'), object_pairs_hook=_unique,
                      parse_constant=lambda x: (_ for _ in ()).throw(ValueError('nonfinite JSON')))


def enabled(manifest):
    marker = manifest.get(MARKER)
    if marker is None:
        if {'manual_surface_actual_projection','manual_surface_projection_refresh'} & manifest.keys():
            raise ValueError('SURFACE_CONTRACT: projection without required contract')
        return False
    if marker != PROJECT_MARKER:
        raise ValueError('SURFACE_CONTRACT: unsupported surface format; no automatic conversion')
    refresh = _fields(manifest.get('manual_surface_projection_refresh'),
        ('contract','automatic_decoder_cache_revalidated','raw_actual_inputs','raw_decoder_seals','origin_manifest_sha256'),'projection origin')
    import actual_review as ar
    names={ar.OCCURRENCE_OVERRIDE_FILE,ar.USER_GLYF_FILE,ar.USER_CFF_FILE,ar.GLYPH_CONFLICT_FILE,ar.GLYPH_PROVENANCE_FILE}
    if (refresh['contract'] != PROJECTION or type(refresh['automatic_decoder_cache_revalidated']) is not bool
            or not isinstance(refresh['raw_actual_inputs'],dict) or set(refresh['raw_actual_inputs']) != names):
        raise ValueError('SURFACE_CONTRACT: missing/unknown projection origin')
    for value in refresh['raw_actual_inputs'].values():
        _sha(value,'raw actual origin')
    _text(refresh['raw_decoder_seals'],'raw decoder origin')
    _sha(refresh['origin_manifest_sha256'],'explicit conversion origin manifest')
    return True


def empty(session_id):
    return {'schema': CONTRACT, 'session_id': _text(session_id, 'session_id'),
            'revision': 0, 'records': [], 'pending': [], 'history': []}


def validate_proof(proof):
    _fields(proof, ('schema','session_id','source','composition','source_seqno','glyph_ordinal',
        'source_coverage_pixels','source_contribution_pixels','replay_sha256','render','raw_reason',
        'raw_overlay_status','automatic_actual','donor_eligible','proof_sha256'), 'proof')
    if proof['schema'] != 'surface-visual-proof/1' or proof['donor_eligible'] is not False:
        raise ValueError('SURFACE_CONTRACT: unsupported proof/donor claim')
    for key in ('source_seqno','glyph_ordinal','source_coverage_pixels','source_contribution_pixels'):
        if type(proof[key]) is not int or proof[key] < (1 if key.endswith('pixels') else 0):
            raise ValueError('SURFACE_CONTRACT: invalid native contribution')
    source = proof['source']
    for key, pattern in [('occurrence_id','occ_[0-9a-f]{64}'),('review_id','rev_[0-9a-f]{64}')]:
        if re.fullmatch(pattern, str(source.get(key,''))) is None:
            raise ValueError('SURFACE_CONTRACT: invalid existing identity')
    _sha(source.get('pdf_sha256'), 'PDF SHA')
    if type(source.get('physical_page')) is not int or source['physical_page'] < 1:
        raise ValueError('SURFACE_CONTRACT: invalid physical page')
    if (not isinstance(source.get('bbox'), list) or len(source['bbox']) != 4
            or any(type(x) not in (int,float) or not math.isfinite(x) for x in source['bbox'])):
        raise ValueError('SURFACE_CONTRACT: invalid location')
    render = _fields(proof['render'], ('page_scale','target_scale','annots','target_rect',
        'page_png','target_png','page_sha256','target_sha256'), 'render')
    if render['annots'] is not True or render['page_scale'] != 1.5 or render['target_scale'] != 4:
        raise ValueError('SURFACE_CONTRACT: unknown render domain')
    for prefix in ('page','target'):
        encoded = render[prefix+'_png']
        if not isinstance(encoded,str) or len(encoded) > 32_000_000:
            raise ValueError('SURFACE_CONTRACT: render exceeds bounded size')
        raw = base64.b64decode(encoded, validate=True)
        if not raw.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(raw).hexdigest() != render[prefix+'_sha256']:
            raise ValueError('SURFACE_CONTRACT: damaged original render')
    _sha(proof['proof_sha256'], 'proof digest')
    if proof['proof_sha256'] != digest({k:v for k,v in proof.items() if k!='proof_sha256'}):
        raise ValueError('SURFACE_CONTRACT: proof digest differs')
    return proof


def validate_record(record):
    _fields(record, ('proof','reading','confirmation','receipt_sha256'), 'record')
    from occurrence_ledger import canonical_bopomofo
    if not record['reading'] or canonical_bopomofo(record['reading']) != record['reading']:
        raise ValueError('SURFACE_CONTRACT: reading must be explicitly entered canonical Bopomofo')
    validate_proof(record['proof'])
    confirmation = _fields(record['confirmation'], ('whole_unit_readable','target_character_matches',
        'reading_from_original','actor','confirmed_at'), 'manual confirmation')
    if any(confirmation[key] is not True for key in
           ('whole_unit_readable','target_character_matches','reading_from_original')):
        raise ValueError('SURFACE_CONTRACT: final whole-unit confirmation required')
    _text(confirmation['actor'], 'actor')
    when = datetime.fromisoformat(confirmation['confirmed_at'])
    if when.tzinfo is None:
        raise ValueError('SURFACE_CONTRACT: confirmation time must have timezone')
    if record['receipt_sha256'] != digest({k:v for k,v in record.items() if k!='receipt_sha256'}):
        raise ValueError('SURFACE_CONTRACT: confirmation receipt changed')
    return record


def validate_document(document, *, session_id=None):
    _fields(document, ('schema','session_id','revision','records','pending','history'), 'document')
    if (document['schema'] != CONTRACT or type(document['revision']) is not int
            or document['revision'] < 0 or (session_id is not None and document['session_id'] != session_id)):
        raise ValueError('SURFACE_CONTRACT: version/session/revision differs')
    _text(document['session_id'],'session_id')
    for lane in ('records','pending'):
        if not isinstance(document[lane],list) or len(document[lane]) > 100_000:
            raise ValueError('SURFACE_CONTRACT: invalid occurrence list')
        seen = set()
        for item in document[lane]:
            validate_record(item)
            proof = item['proof']; oid = proof['source']['occurrence_id']
            if proof['session_id'] != document['session_id'] or oid in seen:
                raise ValueError('SURFACE_CONTRACT: duplicate/foreign occurrence')
            seen.add(oid)
    if not isinstance(document['history'],list) or len(document['history']) > 100_000:
        raise ValueError('SURFACE_CONTRACT: invalid provenance history')
    for item in document['history']:
        validate_record(item)
        if item['proof']['session_id'] != document['session_id']:
            raise ValueError('SURFACE_CONTRACT: foreign provenance history')
    return document


def load(root, manifest):
    path = Path(root)/FILE
    is_enabled = enabled(manifest)
    if not is_enabled:
        if path.exists():
            raise ValueError('SURFACE_CONTRACT: orphan surface store; explicit conversion required')
        return None
    if not path.is_file() or path.is_symlink() or path.is_junction():
        raise ValueError('SURFACE_CONTRACT: required surface store missing/untrusted')
    return validate_document(parse(path.read_bytes()), session_id=manifest['session_id'])


def bytes_for(document):
    validate_document(document)
    return (json.dumps(document,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n').encode('utf-8')


def record_for(proof, reading, *, actor, whole_unit_readable, target_character_matches, reading_from_original):
    from occurrence_ledger import canonical_bopomofo
    record = {'proof':copy.deepcopy(proof),'reading':canonical_bopomofo(reading),
              'confirmation':{'whole_unit_readable':whole_unit_readable,
                              'target_character_matches':target_character_matches,
                              'reading_from_original':reading_from_original,
                              'actor':_text(actor,'actor'),
                              'confirmed_at':datetime.now(timezone.utc).isoformat(timespec='microseconds')}}
    record['receipt_sha256'] = digest(record)
    return validate_record(record)


def verify_sources(document, manifest, output_dir, *, reproof=True):
    if document is None:
        return
    from actual_review import _resolve_pdf_path
    from actual_source_visual_safety import surface_visual_proof, source_visual_operation
    roster = {item['occurrence_id']:item for item in manifest['records']}
    with source_visual_operation():
        for item in [*document['records'],*document['pending'],*document['history']]:
            proof = item['proof']; source = proof['source']
            target = roster.get(source['occurrence_id'])
            if target is None or target['review_id'] != source['review_id']:
                raise ValueError('SURFACE_CONTRACT: occurrence/review is not in sealed roster')
            if reproof:
                current = surface_visual_proof(target, pdf=_resolve_pdf_path(target,output_dir),
                                               session_id=manifest['session_id'])
                # Automatic reading is retained origin audit. A future legal
                # decoder run can change that value without changing PDF ink;
                # it cannot change any native/render binding of this receipt.
                keys = set(proof)-{'automatic_actual','proof_sha256'}
                if any(current[key] != proof[key] for key in keys):
                    raise ValueError('SURFACE_CONTRACT: source/composition/render proof changed')


def applied_digest(document):
    # Pending is staging, not authoritative actual evidence. Independent
    # expected decisions may still save while only this queue changes.
    return digest({'schema':CONTRACT,'session_id':document['session_id'],
                   'records':document['records']})


def dependencies(document):
    by_pdf = {}
    for item in document['records']:
        source=item['proof']['source']
        by_pdf.setdefault(source['pdf_sha256'],[]).append({
            'occurrence_id':source['occurrence_id'],'review_id':source['review_id'],
            'reading':item['reading'],'receipt_sha256':item['receipt_sha256']})
    return {pdf:digest({'schema':PROJECTION,'records':sorted(items,key=lambda x:x['occurrence_id'])})
            for pdf,items in sorted(by_pdf.items())}


def raw_dependencies(root):
    """Raw decoder evidence, independent of this post-decoder projection."""
    import actual_review as ar
    names=(ar.OCCURRENCE_OVERRIDE_FILE,ar.USER_GLYF_FILE,ar.USER_CFF_FILE,
           ar.GLYPH_CONFLICT_FILE,ar.GLYPH_PROVENANCE_FILE)
    result={}
    for name in names:
        path=Path(root)/name
        if not path.is_file():
            raise ValueError('SURFACE_CONTRACT: required raw evidence missing: '+name)
        result[name]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def hold_completion(manifest, gate):
    """A local surface projection cannot certify current raw decoder coverage."""
    if not enabled(manifest) or manifest['manual_surface_projection_refresh']['automatic_decoder_cache_revalidated']:
        return gate
    result=copy.deepcopy(gate)
    name='surface_projection_raw_decoder_revalidated'
    result.setdefault('hard_gates',{})[name]=False
    result['failed_gates']=list(dict.fromkeys([*result.get('failed_gates',[]),name]))
    result.update(complete=False,status='PROCESSING_FINISHED',
                  surface_scope='manual occurrence projection; current full raw decoder validation not performed')
    return result


def projection(document):
    return {'schema':PROJECTION,'dependencies':dependencies(document),
            'records':[{'occurrence_id':x['proof']['source']['occurrence_id'],
                        'review_id':x['proof']['source']['review_id'],'reading':x['reading'],
                        'proof_sha256':x['proof']['proof_sha256'],'receipt_sha256':x['receipt_sha256']}
                       for x in document['records']]}


def apply_projection(manifest, records):
    if not enabled(manifest):
        return records
    value = _fields(manifest.get('manual_surface_actual_projection'),('schema','dependencies','records'),'projection')
    if value['schema'] != PROJECTION:
        raise ValueError('SURFACE_CONTRACT: unsupported projection')
    from occurrence_ledger import canonical_bopomofo, refresh_derived_state
    by_oid = {item['occurrence_id']:index for index,item in enumerate(records)}
    output = list(records); seen=set()
    for claim in value['records']:
        _fields(claim,('occurrence_id','review_id','reading','proof_sha256','receipt_sha256'),'projection record')
        oid=claim['occurrence_id']
        if oid in seen or oid not in by_oid:
            raise ValueError('SURFACE_CONTRACT: projection occurrence missing/duplicate')
        seen.add(oid); index=by_oid[oid]; row=dict(output[index])
        if claim['review_id'] != row['review_id'] or canonical_bopomofo(claim['reading']) != claim['reading']:
            raise ValueError('SURFACE_CONTRACT: projection identity/reading differs')
        row.update(automatic_actual_before_surface=row.get('actual'), actual=claim['reading'],
                   actual_status='RESOLVED', actual_evidence='occurrence-local manual final-surface confirmation',
                   manual_surface_actual=copy.deepcopy(claim), raw_source_warning_retained=True,
                   surface_donor_eligible=False)
        output[index]=refresh_derived_state(row,preserve_confirmed=False)
    return output
