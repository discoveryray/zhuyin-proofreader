"""Finite view substitutes for review/inspector handler regression tests.

No Tcl interpreter, widgets, display or image objects are created. These objects
only hold button/text/selection state and deliver explicitly queued callbacks;
product handlers, readers, preview decoding and durable services remain real.
"""
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import patch
import copy
import tkinter as tk
from tkinter import ttk

import review_display as display
import review_gui as gui
import standalone_proofread as sp
from review_save_service import ReviewSaveService
from tests.review_save_test_support import QueuedRoot


class Value:
    def __init__(self, master=None, value=""):
        self.value, self.listeners = value, []

    def get(self):
        return self.value

    def set(self, value):
        self.value = value
        for callback in self.listeners:
            callback()

    def trace_add(self, _kind, callback):
        self.listeners.append(callback)


class Surface(QueuedRoot):
    def __init__(self, master=None, **options):
        QueuedRoot.__init__(self)
        self.master = master
        self.options = {"state": "normal", "text": "", **options}
        self.children = []
        self.exists = True
        self.report_callback_exception = lambda *args: None
        if master is not None:
            master.children.append(self)
            self.callbacks = master.callbacks
        self._timer_owner = self if master is None else master._timer_owner

    def after(self, delay, callback):
        # Use one monotonically increasing ID namespace per surface tree.
        return QueuedRoot.after(self._timer_owner, delay, callback)

    def after_idle(self, callback):
        return self.after(0, callback)

    def bind(self, event, callback, *, add=None):
        self.bindings.setdefault(event, []).append(callback)

    def event_generate(self, event, **_options):
        if self.exists:
            for callback in self.bindings.get(event, ()):
                callback(SimpleNamespace(widget=self))

    def destroy(self):
        if not self.exists:
            return
        for child in list(self.children):
            child.destroy()
        self.event_generate("<Destroy>")
        self.exists = False
        if self.master is not None and self in self.master.children:
            self.master.children.remove(self)

    def winfo_exists(self):
        return self.exists

    def winfo_children(self):
        return self.children[:]

    def configure(self, **options):
        self.options.update(options)

    config = configure

    def cget(self, key):
        return self.options.get(key, "")

    def __getitem__(self, key):
        return self.cget(key)

    def invoke(self):
        if self.exists and self.cget("state") != "disabled":
            variable = self.options.get("variable")
            if variable is not None:
                variable.set(not variable.get())
            return self.options.get("command", lambda: None)()

    def instate(self, states):
        return all((self.cget("state") == "disabled") if state == "disabled"
                   else (self.cget("state") != "disabled") for state in states)

    def set_items(self, items):
        self.items = list(items)

    def pack(self, **_options):
        pass

    grid = pack
    pack_forget = pack

    def protocol(self, *_args):
        pass

    title = geometry = resizable = transient = minsize = maxsize = protocol
    grab_set = grab_release = focus_set = focus_force = lift = protocol
    update_idletasks = protocol
    wait_visibility = protocol

    def wait_window(self, *_args):
        self.update()

    def winfo_screenwidth(self):
        return 1280

    def winfo_screenheight(self):
        return 900

    def winfo_ismapped(self):
        return self.exists

    winfo_viewable = winfo_ismapped

    def winfo_rootx(self):
        return 0

    winfo_rooty = winfo_rootx

    def winfo_width(self):
        return 1000

    def winfo_height(self):
        return 1000

    def reveal(self, *_args):
        pass

    request_layout = flush_layout = reveal
    refresh = reveal

    def start(self, *_args):
        pass

    stop = start


class Text(Surface):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.content = ""

    def delete(self, *_args):
        self.content = ""

    def insert(self, _position, value):
        self.content += value

    def get(self, *_args):
        return self.content

    def yview(self, *_args):
        return (0, 1)

    def set(self, *_args):
        pass


class Table(Surface):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.rows, self.selected = {}, ()

    def insert(self, _parent, _index, *, iid, values):
        self.rows[iid] = values

    def delete(self, *ids):
        for identity in ids:
            self.rows.pop(identity, None)
        self.selected = tuple(i for i in self.selected if i in self.rows)

    def get_children(self):
        return tuple(self.rows)

    def selection_set(self, value):
        self.selected = (value,)

    def selection(self):
        return self.selected

    def heading(self, *_args, **_kwargs):
        pass

    column = heading

    def yview(self, *_args):
        return (0, 1)


class Container(Surface):
    def add(self, *_args, **_options):
        pass


class Scrollbar(Surface):
    def set(self, *_args):
        pass


@contextmanager
def inspector_views():
    """Replace only native view creation; inspector constructors run unchanged."""
    with ExitStack() as stack:
        for name in ("__init__", "after", "after_idle", "after_cancel", "bind", "event_generate", "destroy", "update",
                     "winfo_exists", "winfo_children", "winfo_screenwidth", "winfo_screenheight",
                     "title", "geometry", "protocol", "lift"):
            stack.enter_context(patch.object(tk.Toplevel, name, getattr(Surface, name)))
        stack.enter_context(patch.object(tk, "StringVar", Value))
        stack.enter_context(patch.object(tk, "Text", Text))
        for name in ("Frame", "Label", "Button", "Combobox", "Entry"):
            stack.enter_context(patch.object(ttk, name, Surface))
        for name in ("Panedwindow", "Notebook"):
            stack.enter_context(patch.object(ttk, name, Container))
        stack.enter_context(patch.object(ttk, "Scrollbar", Scrollbar))
        stack.enter_context(patch.object(ttk, "Treeview", Table))
        yield


class Preview(Surface, display.OccurrencePreview):
    """Real load/render/failure logic; only native canvas/image drawing retires."""
    def __init__(self, parent=None, *, on_failure=None, expand_content=False,
                 on_locate=None, on_layout=None, before_locate=None, **_options):
        Surface.__init__(self, parent)
        self.canvas = Surface(self, height=120)
        self.notice = Surface(self)
        self.on_failure, self.on_locate = on_failure, on_locate
        self.on_layout, self.before_locate = on_layout, before_locate
        self.expand_content, self._minimum_height = expand_content, 120
        self.photo = self.target = self.pixmap = None
        self._draw_pending = self._render_pending = self._locate_pending = None
        self._render_width = self._entry = None
        self._disposed, self._needs_locate = False, False
        self.canvas.find_withtag = lambda tag: (1,) if (
            self.photo is not None and (tag == 'page' or tag == 'target' and self.target is not None)) else ()
        self.canvas.coords = lambda _tag: list(self.target or ())
        self.canvas.canvasx = self.canvas.canvasy = lambda value: value
        self.canvas.find_all = lambda: self.canvas.find_withtag('page')
        self.canvas.winfo_rooty = lambda: 1100 if parent is not None and parent.options.get('sample_index', 0) else 0
        self.canvas.delete = lambda *_args: None
        self.canvas.yview_moveto = lambda *_args: None

    def _draw(self):
        # Native image/canvas boundary: use the decoder's real pixel buffer.
        self._cancel_draw()
        if self._disposed:
            return
        self.photo = self.pixmap

    def destroy(self):
        self._dispose(SimpleNamespace(widget=self.canvas))
        Surface.destroy(self)


class Menu:
    def __init__(self):
        self.commands = {}

    def index(self, label):
        return label

    def invoke(self, label):
        return self.commands[label]()


class ReviewApp(gui.ReviewApp):
    """Real review handlers/services with finite controls instead of a layout."""
    def __init__(self, root, output_dir):
        self.root, self.output_dir = root, output_dir
        self.manifest = sp.json_load_strict(output_dir / "校對工作階段.json")
        sp.validate_manifest_integrity(self.manifest)
        sp.validate_output_artifact_hashes(self.manifest)
        self.db = sp.load_or_initialize_db(output_dir)
        self.records, self.index = [], 0
        self.focused_entry = None
        self._focused_from_undo = False
        self.last_expected_review_id = None
        self._save_in_progress = False
        self._project_generation = 0
        self._save_service = ReviewSaveService(output_dir)
        self._save_recovery_required = False
        self.last_save_timings = {}
        self.deferred_items = set()
        self.staged_checked_occurrence_ids = set()
        self.waiting_actual_occurrence_ids = set()
        for name in ("status", "summary_text", "primary", "secondary", "later", "more_button",
                     "apply_actual_button", "staging_status", "revisit_button", "confirmed_button",
                     "undo_expected_button", "navigation", "decision_actions", "body_canvas"):
            setattr(self, name, Surface(root))
        self.undo_expected_button.configure(command=self.undo_last_expected)
        self.tech_text = Text(root)
        self.more_menu = Menu()
        self.more_menu.commands["返回待辦"] = self.return_to_pending
        self.image = Preview(root, on_failure=self._preview_failed)
        self.reload_records()
        self.show()


class ExpectedDialog(Surface, gui.ExpectedDialog):
    def __init__(self, parent, entry, _title, *, wait=True):
        Surface.__init__(self, parent)
        self.result, self.entry = None, entry
        self.expected, self.evidence, self.reason = Value(), Value(), Value()
        source = entry.get("source_record") or {}
        self.context = Value(value=f"{source.get('局部詞境', '')}；{source.get('所在行', '')}；{entry.get('char', '')}")
        self.bind("<Control-Return>", lambda _event: self.submit())


class ConfirmedDialog(Surface, gui.ConfirmedItemsDialog):
    refresh = gui.ConfirmedItemsDialog.refresh
    def __init__(self, parent, entries, *, wait=True):
        Surface.__init__(self, parent)
        self.result, self.entries = None, list(entries)
        self.pdf_filter, self.page_filter, self.text_filter = Value(), Value(), Value()
        self.table = Table(self)
        self.refresh()


class ActualDialog(Surface, gui.ActualReadingDialog):
    def __init__(self, parent, entry, group, output_dir, *, wait=True, verified_checked_occurrence_ids=()):
        Surface.__init__(self, parent)
        self.result, self.entry, self.group, self.output_dir = None, entry, group, output_dir
        self.samples = gui.actual_review_samples(entry, group, verified_checked_occurrence_ids=verified_checked_occurrence_ids)
        self.verified_checked_occurrence_ids = set(verified_checked_occurrence_ids)
        self.previously_checked = [index > 0 and sample['occurrence_id'] in self.verified_checked_occurrence_ids
                                   for index, sample in enumerate(self.samples)]
        self.previews = [None] * len(self.samples)
        self.sample_available = [False] * len(self.samples)
        self._viewed_target = [False] * len(self.samples)
        self.checked_vars = [Value(value=False) for _ in self.samples]
        self.reading, self.note = Value(), Value()
        self.check_buttons = [None if previous else Surface(self, state="disabled", variable=value)
                              for previous, value in zip(self.previously_checked, self.checked_vars)]
        self.preview_slots = [Surface(self, sample_index=index) for index in range(len(self.samples))]
        self.preview_buttons = [Surface(self) for _ in self.samples]
        self.body_canvas = Surface(self)
        self._preview_ready_after = [None] * len(self.samples)
        self._visibility_after, self._active_peer = None, None
        self._destroying = False
        with patch.object(gui, "OccurrencePreview", Preview):
            if self.samples:
                self.show_sample_preview(0)
            if len(self.samples) == 2:
                self.show_sample_preview(1)

    def reveal_sample(self, index):
        """Supply viewport input; execute the real visibility gate afterward."""
        self.previews[index].canvas.winfo_rooty = lambda: 0
        self._check_target_visibility()

    def destroy(self):
        self._cancel_owned_preview_callbacks()
        Surface.destroy(self)
