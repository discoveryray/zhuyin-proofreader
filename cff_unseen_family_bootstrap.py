from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

_TONE_MARKS = set('ˊˇˋ˙')


def cff_variant(font_name: str) -> str:
    """Return a conservative annotation-subfont variant code.

    PoIn1/2/3 are kept separate because the same CID can differ across these
    subfonts.  Everything else is treated as the standard annotation variant Z.
    An unrecognized naming convention therefore does not invent a new relation;
    the second, gid-only consensus gate below still has to agree before any seed
    is admitted.
    """
    s = str(font_name or '')
    m = re.search(r'PoIn([123])', s, re.I)
    return f'P{m.group(1)}' if m else 'Z'


def cff_unseen_family_key(font_name: str) -> str:
    """Build an ephemeral family namespace without using a persistent mapping.

    Only explicit annotation-variant tokens are normalized.  Weight/style and
    every other part of the font name are preserved so unrelated CFF fonts are
    not merged merely because their glyph IDs happen to overlap.
    """
    s = str(font_name or '').strip()
    s = re.sub(r'^[A-Z]{6}\+', '', s)
    s = re.sub(r'PoIn[123]', '{PHON}', s, flags=re.I)
    s = re.sub(r'ZhuIn', '{PHON}', s, flags=re.I)
    s = re.sub(r'BPMF|BPMW', '{PHON}', s, flags=re.I)
    return s.upper()


def load_crossfamily_consensus(path: Path):
    variant_ref = {}
    gid_ref = {}
    if not path.exists():
        return variant_ref, gid_ref
    with path.open('r', encoding='utf-8-sig', newline='') as f:
        for row in csv.DictReader(f):
            if (row.get('conflict') or '').strip().upper() == 'Y':
                continue
            body = (row.get('body') or '').strip()
            if not body or any(ch in _TONE_MARKS for ch in body):
                continue
            try:
                gid = int(row.get('glyph_id') or '')
                support = int(row.get('style_support_count') or 0)
            except Exception:
                continue
            rec = {
                'body': body,
                'style_support_count': support,
                'style_groups': (row.get('style_groups') or '').strip(),
                'occurrence_count': int(row.get('occurrence_count') or 0),
            }
            mode = (row.get('reference_mode') or '').strip()
            if mode == 'variant_gid':
                variant = (row.get('variant') or '').strip()
                if variant:
                    variant_ref[(variant, gid)] = rec
            elif mode == 'gid':
                gid_ref[gid] = rec
    return variant_ref, gid_ref


def _seed_for_occurrence(item, variant_ref, gid_ref):
    gid = int(item['glyph_id'])
    variant = item['variant']
    a = variant_ref.get((variant, gid))
    b = gid_ref.get(gid)
    # v4.5 hard gate: both independent reference views must be present and must
    # agree exactly on the body.  A single lookup never seeds a target family.
    if not a or not b or a['body'] != b['body']:
        return None
    body = a['body']
    sigs = tuple(item.get('signatures') or ())
    if not sigs or len(sigs) != len(body):
        return None
    return {
        'body': body,
        # Use the weaker of the two views for the safety threshold.
        'source_support': min(a['style_support_count'], b['style_support_count']),
        'variant_styles': a.get('style_groups', ''),
        'gid_styles': b.get('style_groups', ''),
    }


def bootstrap_unseen_families(occurrences, variant_ref, gid_ref, min_source_styles: int = 2):
    """Derive temporary exact-signature maps for unseen CFF families.

    No mapping is written to disk.  A target signature is accepted only when:
      1. its proposal is conflict-free inside the target family, and
      2. either it is independently seen with the same proposed symbol in at
         least two distinct target glyph IDs, or the proposal has at least
         ``min_source_styles`` known-family support in *both* reference views.

    Final decoding still requires every signature in the glyph to hit this
    temporary exact map.  Any conflict or missing evidence is an abstention.
    """
    by_family = defaultdict(list)
    for item in occurrences:
        by_family[item['family_key']].append(item)

    family_maps = {}
    audit_rows = []
    family_stats = {}

    for family_key, items in sorted(by_family.items()):
        evidence = defaultdict(lambda: defaultdict(list))
        seeded_occ = 0
        for item in items:
            seed = _seed_for_occurrence(item, variant_ref, gid_ref)
            if not seed:
                continue
            seeded_occ += 1
            for sig, symbol in zip(item['signatures'], seed['body']):
                evidence[sig][symbol].append({
                    'glyph_id': int(item['glyph_id']),
                    'variant': item['variant'],
                    'source_support': int(seed['source_support']),
                    'variant_styles': seed['variant_styles'],
                    'gid_styles': seed['gid_styles'],
                    'font': item['font'],
                })

        sig_map = {}
        conflict_count = 0
        accepted_count = 0
        for sig, proposals in sorted(evidence.items()):
            labels = sorted(proposals)
            if len(labels) != 1:
                conflict_count += 1
                audit_rows.append({
                    'family_key': family_key, 'signature': sig, 'symbol': '',
                    'status': '棄權：同一簽名出現互斥標籤',
                    'target_gid_count': len({x['glyph_id'] for lst in proposals.values() for x in lst}),
                    'max_source_support': max((x['source_support'] for lst in proposals.values() for x in lst), default=0),
                    'evidence_count': sum(len(lst) for lst in proposals.values()),
                    'source_styles': '', 'fonts': '|'.join(sorted({x['font'] for lst in proposals.values() for x in lst})),
                })
                continue
            symbol = labels[0]
            lst = proposals[symbol]
            gid_count = len({x['glyph_id'] for x in lst})
            max_source_support = max((x['source_support'] for x in lst), default=0)
            accepted = gid_count >= 2 or max_source_support >= int(min_source_styles)
            if accepted:
                sig_map[sig] = symbol
                accepted_count += 1
                status = '接受：暫存exact簽名'
            else:
                status = '棄權：證據未達安全門檻'
            styles = sorted({s for x in lst for s in (x['variant_styles']+'|'+x['gid_styles']).split('|') if s})
            audit_rows.append({
                'family_key': family_key, 'signature': sig, 'symbol': symbol if accepted else '',
                'status': status, 'target_gid_count': gid_count,
                'max_source_support': max_source_support, 'evidence_count': len(lst),
                'source_styles': '|'.join(styles), 'fonts': '|'.join(sorted({x['font'] for x in lst})),
            })

        family_maps[family_key] = sig_map
        family_stats[family_key] = {
            'occurrences': len(items), 'seeded_occurrences': seeded_occ,
            'candidate_signatures': len(evidence), 'accepted_signatures': accepted_count,
            'conflict_signatures': conflict_count,
        }

    return family_maps, audit_rows, family_stats


def decode_with_bootstrap(item, signature_map):
    sigs = tuple(item.get('signatures') or ())
    if not sigs or not signature_map or not all(s in signature_map for s in sigs):
        return ''
    tone = str(item.get('tone') or '')
    if tone == '?':
        return ''
    body = ''.join(signature_map[s] for s in sigs)
    if not body:
        return ''
    return ('˙' + body) if tone == '˙' else (body + tone)
