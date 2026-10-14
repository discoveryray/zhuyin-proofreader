"""Source-specific paint and warning boundaries, with real isolated PDF bytes."""
from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import fitz
import pytest

import actual_review as actual
import global_glyph_promotion as promotion
import review_display as display
import review_gui as gui
import standalone_proofread as pipeline
from actual_source_visual_safety import source_visual_safety
from export_pdf_text_diagnostics import TrueTypeGlyphInspector
from review_operation_worker import preview_png
from tests.test_actual_source_visual_safety import make_visual_source
from tests.test_actual_visual_write_guards import context_for


def painted_source(root, *, color=(0, 0, 0), background=None, painting=None,
                   char="A", origin=(72, 90)):
    """Handwritten expected contours; source claims are from genuine trace/font."""
    original = make_visual_source(root / "font", count=1)[0]
    pdf = root / "different-name.pdf"
    with fitz.open(original["pdf"]) as old:
        font = old.extract_font(original["font_xref"])[3]
    with fitz.open() as document:
        page = document.new_page(width=240, height=260)
        if background is not None:
            page.draw_rect(page.rect, color=None, fill=background)
        xref = page.insert_font(fontname="VisibleSource", fontbuffer=font)
        page.insert_text(origin, char, fontname="VisibleSource", fontsize=20, color=color)
        if painting == "cover":
            page.draw_rect(fitz.Rect(origin[0]-1,origin[1]-11,origin[0]+6,origin[1]+1),
                           color=None,fill=(1,1,1))
        elif painting == "frame":
            page.draw_rect(fitz.Rect(origin[0]-10,origin[1]-20,origin[0]+20,origin[1]+10),
                           color=(0,0,1),width=1)
        elif painting == "neighbor":
            page.insert_text((origin[0]+35,origin[1]),"B",fontname="VisibleSource",fontsize=20)
        elif painting == "rewritten":
            page.draw_rect(fitz.Rect(origin[0]-1,origin[1]-11,origin[0]+13,origin[1]+5),
                           color=None,fill=(1,1,1))
            other = "B" if char == "A" else "A"
            page.insert_text(origin,other,fontname="VisibleSource",fontsize=20)
        document.save(pdf)
    with fitz.open(pdf) as document:
        span = next(s for s in document[0].get_texttrace() if s["chars"][0][0] == ord(char))
        c = span["chars"][0]
        inspector = TrueTypeGlyphInspector(document.extract_font(xref)[3])
        digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
        oid = "occ_"+hashlib.sha256((digest+str(c[2])).encode()).hexdigest()
        rid = "rev_"+hashlib.sha256((oid+"review").encode()).hexdigest()
        return {"pdf":str(pdf),"pdf_name":pdf.name,"pdf_sha256":digest,
            "physical_page":1,"printed_page":"17","char":char,"font_xref":xref,
            "glyph_id":c[1],"zhuyin_component_id":c[1],"occurrence_id":oid,"review_id":rid,
            "actual":"ㄅ","actual_status":"RESOLVED","actual_evidence":"raw fixture",
            "state":"DIFFERENCE_PENDING_CONFIRMATION",
            "expected_set":["ㄆ"],"expected_evidence":"independent expected",
            "source_record":{"font_xref":xref,"glyph_id_字形索引":c[1],"注音元件ID":c[1],
                "TTF字形SHA256":inspector.glyph_sha256(c[1])},
            **dict(zip(("x0","y0","x1","y1"),c[3]))}


def test_final_visibility_requires_source_contribution_and_allows_white_contrast(tmp_path):
    for name,color,background,allowed in (
            ("black-white",(0,0,0),None,True),
            ("white-white",(1,1,1),None,False),
            ("black-black",(0,0,0),(0,0,0),False),
            ("white-black",(1,1,1),(0,0,0),True),
            ("same-gray",(.4,.4,.4),(.4,.4,.4),False)):
        row = painted_source(tmp_path/name,color=color,background=background)
        with fitz.open(row["pdf"]) as document:
            # The fixed glyph's own solid interior is distinct from page noise,
            # neighbors or a later frame. These values are independent oracles.
            interior = document[0].get_pixmap(matrix=fitz.Matrix(4,4),
                clip=fitz.Rect(73,82,75,88),alpha=False)
            outside = document[0].get_pixmap(matrix=fitz.Matrix(4,4),
                clip=fitz.Rect(110,82,112,88),alpha=False)
            assert (interior.samples != outside.samples) is allowed
            if name == "white-white":
                assert all(v == 255 for v in document[0].get_pixmap(alpha=False).samples)
        result = source_visual_safety(row)
        assert result["location_readable"] is allowed
        assert result["donor_eligible"] is allowed
        if not allowed:
            assert not result["donor_eligible"]


def test_generic_overlay_and_normal_frame_neighbor_use_original_warning_slot(tmp_path):
    for char,origin in (("A",(72,90)),("B",(145,175))):
        for painting in ("cover","rewritten","frame","neighbor"):
            row = painted_source(tmp_path/(char+painting),char=char,origin=origin,painting=painting)
            allowed = painting in ("frame","neighbor")
            result = source_visual_safety(row)
            assert result["location_readable"] is allowed
            assert result["donor_eligible"] is allowed
            details = display.occurrence_preview(row,display_width=300,return_details=True)
            assert (details["target"] is not None) is allowed
            assert (details["warning_target"] is not None) is not allowed
            assert row["pdf_name"] in details["notice"] and "實體頁：1" in details["notice"]
            if not allowed:
                assert result["overlay_status"] in ("CONFIRMED","SUSPECTED")
                assert "不能確認" in details["notice"]
                # Crop padding follows the original locator; no neighboring
                # same-character search or new occurrence identity is involved.
                assert details["diagnostic"]["warning_bbox"] == [row[k] for k in ("x0","y0","x1","y1")]


def test_warning_box_cannot_unlock_actual_or_global_and_refusal_is_prewrite(tmp_path):
    row = painted_source(tmp_path/"blank",color=(1,1,1))
    before = copy.deepcopy(row)
    group = actual.build_actual_group_for_entry([row],row)
    root = tmp_path/"actual"
    value = preview_png(row,padding=(20,20),output_dir=None,display_width=240)
    assert value["target"] is None and value["warning_target"] is not None
    assert value["diagnostic"]["location_readable"] is False
    for write in (actual.stage_manual_actual_group,actual.apply_verified_actual_group):
        with pytest.raises(ValueError):
            write(root,group,"ㄅ",checked_occurrence_ids=[row["occurrence_id"]],source="checked warning")
        assert not root.exists()
    initialized=[]
    with pytest.raises(ValueError):
        actual.apply_direct_visual_actual_batch(root,[{"group":group,"reading":"ㄅ",
            "checked_occurrence_ids":[row["occurrence_id"]],"source":"checked warning"}],
            source_context=context_for([row]),initialize_evidence=lambda:initialized.append(True))
    assert initialized == [] and not root.exists()
    intents,admissions=promotion.direct_visual_intents(group,"ㄅ",[row["occurrence_id"]],context_for([row]))
    assert not intents and admissions[0]["status"] == "NON_GLOBAL_ELIGIBLE"
    with pytest.raises(ValueError):
        pipeline.require_current_actual_visual_source(row,tmp_path)
    event=pipeline.build_manual_expected_event(row,operation="ENTER_EXPECTED",expected_set="ㄆ")
    assert event["manual_expected_decision"]["operation"] == "ENTER_EXPECTED"
    assert row == before


def test_warning_canvas_tag_does_not_call_confirmation_ready(tmp_path):
    from tests.no_window_review_support import Surface,Preview
    row=painted_source(tmp_path/"source",painting="cover")
    details=display.occurrence_preview(row,display_width=200,return_details=True)
    view=Preview(Surface())
    view._entry=copy.deepcopy(row)
    view._render_width=view.canvas.winfo_width()-16
    view._needs_locate=True
    view.pixmap=SimpleNamespace(width=details["pixmap"].width,height=details["pixmap"].height,
        png=details["pixmap"].tobytes("png"))
    view.target=None
    view.warning_target=details["warning_target"]
    rectangles=[]
    view.canvas.create_image=lambda *_args,**_kwargs:None
    view.canvas.yview=lambda:(0.,1.)
    view.canvas.yview_moveto=lambda _fraction:None
    view.canvas.create_rectangle=lambda *coordinates,**options:rectangles.append((coordinates,options))
    view.canvas.find_withtag=lambda tag:tuple(i for i,(_coordinates,options) in enumerate(rectangles,1)
        if options.get("tags") == tag)
    called=[]
    view.on_ready=lambda _view:called.append("ready")
    # Retire only the native Tcl image boundary. Production rendering chooses
    # rectangle/tag/callback/height itself, with genuine source diagnostics.
    with patch.object(display.tk,"PhotoImage",lambda **kw:kw):
        display.OccurrencePreview._draw(view)
    assert view.canvas.find_withtag("warning")
    assert not view.canvas.find_withtag("target") and not called
    assert view.target is None
    view.destroy()


def test_preview_and_final_source_proof_include_annotation_paint_domain(tmp_path):
    for name,region,allowed in (("covered",fitz.Rect(71,79,78,91),False),
                                ("unrelated",fitz.Rect(180,190,200,210),True)):
        row=painted_source(tmp_path/name)
        annotated=tmp_path/name/"annotated.pdf"
        with fitz.open(row["pdf"]) as document:
            annotation=document[0].add_rect_annot(region)
            annotation.set_colors(stroke=(1,1,1),fill=(1,1,1))
            annotation.set_border(width=0)
            annotation.update(opacity=1)
            document.save(annotated)
        row.update(pdf=str(annotated),pdf_name=annotated.name,
            pdf_sha256=hashlib.sha256(annotated.read_bytes()).hexdigest())
        with fitz.open(annotated) as document:
            source=fitz.Rect(73,82,75,88)
            shown=document[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=source,alpha=False)
            content=document[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=source,alpha=False,annots=False)
            assert min(content.samples) < 255  # contents-only is insufficient
            assert (min(shown.samples) < 255) is allowed
        safety=source_visual_safety(row)
        details=display.occurrence_preview(row,display_width=240,return_details=True)
        assert safety["location_readable"] is allowed
        assert safety["donor_eligible"] is allowed
        assert (details["target"] is not None) is allowed
        assert (details["warning_target"] is not None) is not allowed


def test_widget_cover_cannot_hide_behind_contents_only_source_proof(tmp_path):
    row=painted_source(tmp_path/"source")
    pdf=tmp_path/"widget.pdf"
    with fitz.open(row["pdf"]) as document:
        widget=fitz.Widget()
        widget.field_name="isolated-source-cover"
        widget.field_type=fitz.PDF_WIDGET_TYPE_TEXT
        widget.field_value=""
        widget.rect=fitz.Rect(71,79,78,91)
        widget.fill_color=(1,1,1)
        widget.border_color=(1,1,1)
        widget.border_width=0
        document[0].add_widget(widget)
        document.save(pdf)
    row.update(pdf=str(pdf),pdf_name=pdf.name,pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest())
    with fitz.open(pdf) as document:
        assert len(list(document[0].widgets())) == 1
        region=fitz.Rect(73,82,75,88)
        shown=document[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=region,alpha=False)
        plain=document[0].get_pixmap(matrix=fitz.Matrix(4,4),clip=region,alpha=False,annots=False)
        assert all(v==255 for v in shown.samples) and min(plain.samples)<255
    safety=source_visual_safety(row)
    assert not safety["location_readable"] and not safety["donor_eligible"]


def test_operation_cache_reuses_sealed_page_and_cannot_mutate_source_proof(tmp_path):
    import actual_source_visual_safety as safety
    from concurrent.futures import ThreadPoolExecutor
    row=painted_source(tmp_path/"source")
    original_read=Path.read_bytes
    reads=[]
    def counted_read(path):
        if path.resolve() == Path(row["pdf"]).resolve():
            reads.append(path)
        return original_read(path)
    with patch.object(Path,"read_bytes",counted_read), patch.object(safety,"_capture_native_page",
            wraps=safety._capture_native_page) as capture:
        with safety.source_visual_operation() as operation:
            for _ in range(599):
                result=safety.source_visual_safety(row)
                assert result["location_readable"] and result["donor_eligible"]
                result.update(location_readable=False,donor_eligible=False)
            assert capture.call_count==1 and len(reads)==1
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(ValueError,match="OWNER"):
                    executor.submit(operation.read_source,row["pdf"],row["pdf_sha256"]).result()
            operation.recheck_sources()
            assert len(reads)==2
        with pytest.raises(ValueError,match="CLOSED"):
            operation.read_source(row["pdf"],row["pdf_sha256"])
    blank=painted_source(tmp_path/"blank",color=(1,1,1))
    with safety.source_visual_operation():
        refused=safety.source_visual_safety(blank)
        refused.update(location_readable=True,donor_eligible=True)
        assert not safety.source_visual_safety(blank)["location_readable"]


def test_a_only_with_existing_reusable_truth_does_not_touch_unchecked_peers(tmp_path):
    rows=make_visual_source(tmp_path/"source",count=4)
    root=tmp_path/"actual"
    first_group=actual.build_actual_group_for_entry(rows[:2],rows[0])
    promoted=actual.apply_verified_actual_group(root,first_group,"ㄅ",
        checked_occurrence_ids=[r["occurrence_id"] for r in rows[:2]],source="visual")
    assert promoted["propagated_to_group"]
    group=actual.build_actual_group_for_entry(rows,rows[0])
    with patch.object(actual,"actual_visual_source_safety",wraps=actual.actual_visual_source_safety) as proved:
        result=actual.apply_verified_actual_group(root,group,"ㄅ",
            checked_occurrence_ids=[rows[0]["occurrence_id"]],source="visual")
    assert not result["propagated_to_group"]
    assert result["target_occurrence_ids"]==[rows[0]["occurrence_id"]]
    assert all(call.args[0]["occurrence_id"]==rows[0]["occurrence_id"] for call in proved.call_args_list)
    overrides=actual._read_csv(root/actual.OCCURRENCE_OVERRIDE_FILE,actual.OVERRIDE_HEADERS)
    assert {(r["x0"],r["y0"],r["stable_key"]) for r in overrides}=={
        (str(r["x0"]),str(r["y0"]),r["stable_key"]) for r in rows[:2]}


def test_white_source_retained_intent_remains_pending_without_global_mutation(tmp_path):
    import global_exact_glyph_library as library
    row=painted_source(tmp_path/"blank",color=(1,1,1))
    normal=painted_source(tmp_path/"normal")
    group=actual.build_actual_group_for_entry([normal],normal)
    genuine,_=promotion.direct_visual_intents(group,"ㄅ",[normal["occurrence_id"]],context_for([normal]))
    identity,evidence=genuine[0]["payload"]["identity"],genuine[0]["payload"]["evidence"]
    frozen={"session_id":evidence["source_project_id"],"pdf_sha256":row["pdf_sha256"],
        "font_program_sha256":evidence["source_font_program_sha256"],"occurrence_id":row["occurrence_id"],
        "review_id":row["review_id"],"identity":identity,"reading":"ㄅ","confirmation_channel":library.PDF_PAGE_VISUAL}
    retained=library.make_direct_evidence_intent(identity,reading="ㄅ",
        source_project_id=evidence["source_project_id"],source_pdf_sha256=row["pdf_sha256"],
        source_font_program_sha256=evidence["source_font_program_sha256"],
        source_occurrence_id=row["occurrence_id"],source_review_id=row["review_id"],
        decision_snapshot_sha256=library._canonical_sha256(frozen))
    root=tmp_path/"actual"
    promotion.enqueue_promotion_intents(root,[retained])
    outbox=root/promotion.GLOBAL_PROMOTION_OUTBOX_FILE
    before=outbox.read_bytes()
    repository=library.GlobalExactGlyphRepository.resolved(tmp_path/"global")
    result=promotion.deliver_pending_promotion_outbox(root,repository=repository,
        source_context=context_for([row]),source_entries=[row])
    assert result["status"]=="PENDING_RETRY"
    assert outbox.read_bytes()==before and not repository.path.exists()


def test_prewrite_rechecks_cached_source_before_any_staging_file(tmp_path):
    row=painted_source(tmp_path/"source")
    group=actual.build_actual_group_for_entry([row],row)
    root=tmp_path/"actual"
    original=actual.load_manual_actual_staging
    def change_after_proof(path):
        value=original(path)
        pdf=Path(row["pdf"])
        pdf.write_bytes(pdf.read_bytes()+b"\n% isolated late source mutation\n")
        return value
    with patch.object(actual,"load_manual_actual_staging",change_after_proof):
        with pytest.raises(ValueError,match="SOURCE_FILE_CHANGED"):
            actual.stage_manual_actual_group(root,group,"ㄅ",
                checked_occurrence_ids=[row["occurrence_id"]],source="visual")
    assert not root.exists()


def test_preview_child_reuses_page_and_retires_failed_owner(tmp_path):
    import actual_source_visual_safety as safety
    from review_operation_worker import _preview_child
    rows=make_visual_source(tmp_path/"source",count=2)
    payload=lambda row:dict(entry=row,padding=(20,20),output_dir=None,display_width=240)
    requests=iter([payload(rows[1]),payload(dict(rows[0],pdf_sha256="0"*64)),payload(rows[0]),None])
    class Connection:
        def __init__(self): self.sent=[];self.closed=False
        def send(self,value): self.sent.append(value)
        def recv(self): return next(requests)
        def close(self): self.closed=True
    connection=Connection()
    with patch.object(safety,"_capture_native_page",wraps=safety._capture_native_page) as capture:
        _preview_child(connection,payload(rows[0]))
    assert capture.call_count==2
    assert connection.closed and len(connection.sent)==4
    assert [item[1] is None for item in connection.sent]==[True,True,False,True]
    assert connection.sent[0][0]["occurrence_id"]==rows[0]["occurrence_id"]
    assert connection.sent[1][0]["occurrence_id"]==rows[1]["occurrence_id"]
    assert connection.sent[3][0]["target"] is not None


def test_batch_preparation_apply_and_flush_share_one_sealed_page(tmp_path):
    import actual_source_visual_safety as safety
    rows=make_visual_source(tmp_path/"source",count=2)
    group=actual.build_actual_group_for_entry(rows,rows[0])
    before=copy.deepcopy(rows)
    with patch.object(safety,"_capture_native_page",wraps=safety._capture_native_page) as capture:
        result=actual.apply_direct_visual_actual_batch(tmp_path/"actual",
            [{"group":group,"reading":"ㄅ","checked_occurrence_ids":[r["occurrence_id"] for r in rows],
              "source":"visual"}],source_context=context_for(rows))
    assert capture.call_count==1
    assert result["group_results"][0]["propagated_to_group"]
    assert result["project_actual_commit"]=="COMMITTED" and rows==before


def test_subpixel_independent_tone_is_unprovable_before_and_after_erasure(tmp_path):
    original=make_visual_source(tmp_path/"source",count=1,outside_tone=True,tone_size=1)[0]
    erased=tmp_path/"erased.pdf"
    with fitz.open(original["pdf"]) as document:
        document[0].draw_rect(fitz.Rect(53.9,41.8,54.2,42.1),color=None,fill=(1,1,1))
        document.save(erased)
    changed=dict(original,pdf=str(erased),pdf_name=erased.name,
        pdf_sha256=hashlib.sha256(erased.read_bytes()).hexdigest())
    region=fitz.Rect(53.9,41.8,54.2,42.1)
    samples={}
    for name,row in (("before",original),("after",changed)):
        with fitz.open(row["pdf"]) as document:
            samples[name]={scale:document[0].get_pixmap(matrix=fitz.Matrix(scale,scale),
                clip=region,alpha=False).samples for scale in (4,32)}
            assert min(document[0].get_pixmap(alpha=False).samples)<255  # visible body is insufficient
        proof=source_visual_safety(row)
        assert not proof["location_readable"] and not proof["donor_eligible"]
        assert proof["overlay_status"]=="SUSPECTED" and "SUBPIXEL" in proof["reason"]
        details=display.occurrence_preview(row,display_width=240,return_details=True)
        assert details["target"] is None and details["warning_target"] is not None
        group=actual.build_actual_group_for_entry([row],row)
        root=tmp_path/("actual-"+name)
        with pytest.raises(ValueError):
            actual.stage_manual_actual_group(root,group,"ㄅ",
                checked_occurrence_ids=[row["occurrence_id"]],source="visual")
        assert not root.exists()
    assert samples["before"][4]==samples["after"][4]
    assert min(samples["before"][32])<255 and all(v==255 for v in samples["after"][32])


def test_unsampled_later_native_ink_intersection_is_suspected_not_eligible(tmp_path):
    original=painted_source(tmp_path/"normal")
    changed_path=tmp_path/"micro-paint.pdf"
    with fitz.open(original["pdf"]) as document:
        document[0].draw_rect(fitz.Rect(74.031,84.031,74.032,84.032),color=None,fill=(1,1,1))
        document.save(changed_path)
        before=document[0].get_pixmap(matrix=fitz.Matrix(4,4),alpha=False).samples
    # Independent full-page pixels cannot disprove continuous native overlap.
    with fitz.open(original["pdf"]) as document:
        normal=document[0].get_pixmap(matrix=fitz.Matrix(4,4),alpha=False).samples
    with fitz.open(changed_path) as document:
        assert document[0].get_pixmap(matrix=fitz.Matrix(4,4),alpha=False).samples==normal==before
        assert document[0].get_drawings()[-1]["rect"].intersects(fitz.Rect(72,80,77,90))
    row=dict(original,pdf=str(changed_path),pdf_name=changed_path.name,
        pdf_sha256=hashlib.sha256(changed_path.read_bytes()).hexdigest())
    proof=source_visual_safety(row)
    assert not proof["location_readable"] and not proof["donor_eligible"]
    assert proof["overlay_status"]=="SUSPECTED"
    assert proof["reason"]=="SOURCE_LATER_INK_INTERSECTION_UNPROVABLE"
