"""Main-thread event pumps for asynchronous review regression tests."""
import time


class QueuedRoot:
    def __init__(self):
        self.callbacks = []

    def after(self, _delay, callback):
        self.callbacks.append(callback)

    def update(self):
        callbacks, self.callbacks = self.callbacks, []
        for callback in callbacks:
            callback()


def wait_for_save(app, *, timeout=15):
    deadline = time.monotonic() + timeout
    while getattr(app, "_save_in_progress", False):
        if time.monotonic() >= deadline:
            raise AssertionError("background review save did not finish")
        app.root.update()
        time.sleep(0.001)
    app.root.update()
