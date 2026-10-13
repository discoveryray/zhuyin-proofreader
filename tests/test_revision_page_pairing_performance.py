"""Pure page pairing: original frozen oracle, no PDF, Tk or timing gates."""
from collections import defaultdict
from difflib import SequenceMatcher
import copy
import random
import pytest
import revision_diff as rd
from revision_diff import PageData, PagePair

# Frozen b5d02c2 original bodies. Only private oracle names are substituted.
# Raw original and measured prototype source/AST are preserved in stage6 evidence.
def _original_ratio(a: str, b: str) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b, autojunk=False).ratio()

def _original_pair_pages(old_pages: list[PageData], new_pages: list[PageData]) -> tuple[list[PagePair], list[PageData], list[PageData]]:
    pairs: list[PagePair] = []
    used_old: set[int] = set()
    used_new: set[int] = set()

    old_by_label: dict[str, list[int]] = defaultdict(list)
    new_by_label: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(old_pages):
        if p.printed_page:
            old_by_label[p.printed_page].append(i)
    for j, p in enumerate(new_pages):
        if p.printed_page:
            new_by_label[p.printed_page].append(j)

    # 第一層：唯一課本頁碼。為避免改頁後同頁碼硬配錯頁，仍要求最低內容相似度。
    for label in sorted(set(old_by_label) & set(new_by_label)):
        oi = old_by_label[label]
        nj = new_by_label[label]
        if len(oi) == 1 and len(nj) == 1:
            i, j = oi[0], nj[0]
            score = _original_ratio(old_pages[i].chars, new_pages[j].chars)
            if score >= 0.35:
                pairs.append(PagePair(old_pages[i], new_pages[j], "課本頁碼一致", score))
                used_old.add(i); used_new.add(j)

    # 第二層：剩餘頁以注音中文字序列做高相似度配對；不使用注音本身，避免目標洩漏。
    candidates: list[tuple[float, float, int, int]] = []
    maxn = max(len(old_pages), len(new_pages), 1)
    for i, op in enumerate(old_pages):
        if i in used_old or not op.chars:
            continue
        for j, np in enumerate(new_pages):
            if j in used_new or not np.chars:
                continue
            score = _original_ratio(op.chars, np.chars)
            if score < 0.72:
                continue
            order_gap = abs(op.global_index - np.global_index) / maxn
            candidates.append((score, -order_gap, i, j))
    for score, neg_gap, i, j in sorted(candidates, reverse=True):
        if i in used_old or j in used_new:
            continue
        pairs.append(PagePair(old_pages[i], new_pages[j], "內容序列高相似度", score))
        used_old.add(i); used_new.add(j)

    pairs.sort(key=lambda p: (p.old.global_index, p.new.global_index))
    return pairs, [p for i, p in enumerate(old_pages) if i not in used_old], [p for j, p in enumerate(new_pages) if j not in used_new]


def pages(side, texts, *, labels=None, indices=None):
    return [PageData(side, side+'.pdf', 'isolated/'+side+'.pdf', index+1,
                     labels[index] if labels is not None else '',
                     [{'字元': char} for char in text],
                     indices[index] if indices is not None else index)
            for index, text in enumerate(texts)]


def assert_original_oracle(old, new):
    frozen=copy.deepcopy((old,new))
    actual=rd.pair_pages(old,new)
    expected=_original_pair_pages(old,new)
    assert actual==expected  # Every dataclass identity/method/exact score/order/unmatched page.
    assert (old,new)==frozen
    return actual


@pytest.mark.parametrize('matched',[34,35,36])
def test_unique_printed_label_retains_original_low_threshold(matched):
    old=pages('old',['甲'*matched+'乙'*(100-matched)],labels=['1'])
    new=pages('new',['甲'*matched+'丙'*(100-matched)],labels=['1'])
    pairs,unmatched_old,unmatched_new=assert_original_oracle(old,new)
    assert len(pairs)==int(matched>=35)
    if pairs:assert pairs[0].method=='課本頁碼一致' and pairs[0].similarity==matched/100
    else:assert unmatched_old==old and unmatched_new==new


@pytest.mark.parametrize('matched',[71,72,73])
def test_remaining_content_threshold_is_inclusive_and_uses_exact_ratio(matched):
    old=pages('old',['甲'*matched+'乙'*(100-matched)])
    new=pages('new',['甲'*matched+'丙'*(100-matched)])
    pairs,unmatched_old,unmatched_new=assert_original_oracle(old,new)
    assert len(pairs)==int(matched>=72)
    if pairs:assert pairs[0].method=='內容序列高相似度' and pairs[0].similarity==matched/100
    else:assert unmatched_old==old and unmatched_new==new


def test_quick_upper_bound_cannot_replace_exact_ratio():
    old=pages('old',['甲'*50+'乙'*50]);new=pages('new',['乙'*50+'甲'*50])
    matcher=SequenceMatcher(None,old[0].chars,new[0].chars,autojunk=False)
    assert matcher.quick_ratio()==1 and matcher.ratio()==.5
    assert assert_original_oracle(old,new)==([],old,new)


def test_length_upper_bound_skips_only_impossible_remaining_candidates(monkeypatch):
    counts={'real_quick':0,'quick':0,'ratio':0}
    class CountedMatcher(SequenceMatcher):
        def real_quick_ratio(self):counts['real_quick']+=1;return super().real_quick_ratio()
        def quick_ratio(self):counts['quick']+=1;return super().quick_ratio()
        def ratio(self):counts['ratio']+=1;return super().ratio()
    monkeypatch.setattr(rd,'SequenceMatcher',CountedMatcher)
    old=pages('old',['甲'*100]);new=pages('new',['甲'*200])
    assert assert_original_oracle(old,new)==([],old,new)
    assert counts=={'real_quick':1,'quick':0,'ratio':0}


def test_duplicate_content_tie_preserves_reverse_candidate_and_final_global_order():
    old=pages('old',['甲'*100],labels=['DUP'],indices=[10])
    new=pages('new',['甲'*100,'甲'*100],labels=['DUP','DUP'],indices=[9,11])
    pairs,unmatched_old,unmatched_new=assert_original_oracle(old,new)
    assert pairs[0].new is new[1] and unmatched_new==[new[0]] and not unmatched_old
    old=pages('old',['甲'*100,'乙'*100,'丙'*100],indices=[20,0,10])
    new=pages('new',['丙'*100,'甲'*100,'乙'*100],indices=[30,10,20])
    pairs,_,_=assert_original_oracle(old,new)
    assert [(p.old.global_index,p.new.global_index) for p in pairs]==[(0,20),(10,30),(20,10)]


def test_far_moved_page_keeps_full_candidate_space():
    texts=[char*120 for char in '甲乙丙丁戊己庚辛']
    old=pages('old',texts);new=pages('new',texts[1:]+texts[:1])
    pairs,unmatched_old,unmatched_new=assert_original_oracle(old,new)
    assert len(pairs)==8 and not unmatched_old and not unmatched_new
    assert pairs[0].old is old[0] and pairs[0].new is new[7]


@pytest.mark.parametrize('label',['','unique-empty'])
def test_empty_pages_preserve_label_route_and_remaining_abstention(label):
    old=pages('old',[''],labels=[label]);new=pages('new',[''],labels=[label])
    pairs,unmatched_old,unmatched_new=assert_original_oracle(old,new)
    if label:assert len(pairs)==1 and pairs[0].similarity==1.0
    else:assert not pairs and unmatched_old==old and unmatched_new==new


@pytest.mark.parametrize('seed',[7,13,29,41])
def test_mixed_small_inputs_match_full_frozen_algorithm(seed):
    rng=random.Random(seed)
    texts=[''.join(rng.choices('國小教材學生活動閱讀問題',k=32)) for _ in range(8)]
    texts[5]=texts[4];texts[7]=''
    labels=['1','','','','DUP','DUP','','']
    old=pages('old',texts,labels=labels)
    changed=list(texts);changed[3]=changed[3][:24]+'新增頁面內容文字';changed.pop(6);changed.insert(2,'插入其他新頁內容'*5)
    new=pages('new',changed,labels=['1','','','','DUP','DUP','',''])
    assert_original_oracle(old,new)
