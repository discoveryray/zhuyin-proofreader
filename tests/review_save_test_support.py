"""Main-thread event pumps for asynchronous review regression tests."""
import time


class QueuedRoot:
    def __init__(self):
        self.callbacks = {}
        self.bindings = {}
        self.serial = 0

    def after(self, _delay, callback):
        self.serial += 1
        timer_id = f"after-{self.serial}"
        self.callbacks[timer_id] = callback
        return timer_id

    def after_cancel(self, timer_id):
        self.callbacks.pop(timer_id, None)

    def bind(self, event, callback, *, add=None):
        self.bindings[event] = callback

    def update(self):
        for timer_id in list(self.callbacks):
            callback = self.callbacks.pop(timer_id, None)
            if callback is not None:
                callback()


def wait_for_save(app, *, timeout=15):
    deadline = time.monotonic() + timeout
    while getattr(app, "_save_in_progress", False):
        if time.monotonic() >= deadline:
            raise AssertionError("background review save did not finish")
        app.root.update()
        time.sleep(0.001)
    app.root.update()
