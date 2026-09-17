"""Flushed stage messages and elapsed-time heartbeats; never runs scan work."""
import sys
import threading
import time

from reuse_record import as_clock


class Progress:
    def __init__(self, label, *, enabled=True, interval=10, stream=None):
        self.label = label
        self.enabled = enabled
        self.interval = interval
        self.stream = stream if stream is not None else sys.stderr
        self.started = self.stage_started = time.monotonic()
        self.stage = 'starting'
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None

    def _write(self, message):
        elapsed = time.monotonic() - self.started
        duration = f'{elapsed:.1f}s' if elapsed < 60 else as_clock(elapsed)
        try:
            print(f'[progress] {self.label} | {message} | elapsed {duration}',
                  file=self.stream, flush=True)
        except OSError:
            # Optional progress must not fail a scan if its output pipe closes.
            self.enabled = False
            self._stop.set()

    def __call__(self, message):
        if self.enabled:
            with self._lock:
                self.stage, self.stage_started = message, time.monotonic()
                self._write(message)

    def _heartbeat(self):
        while not self._stop.wait(self.interval):
            with self._lock:
                self._write(f'still running: {self.stage} '
                            f'(stage {as_clock(time.monotonic() - self.stage_started)})')

    def __enter__(self):
        if self.enabled:
            self('starting')
            self._thread = threading.Thread(target=self._heartbeat, daemon=True)
            self._thread.start()
        return self

    def close(self):
        self._stop.set()
        if self._thread is not None:
            self._thread.join()

    def finish(self, message):
        self.close()
        self(message)

    def __exit__(self, kind, error, traceback):
        self.close()
        if error is not None:
            self(f'failed during {self.stage}: {error}')
