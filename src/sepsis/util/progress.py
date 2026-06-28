"""Progress + ETA logging (H2 common util — h2_handoff.md §0).

Long-running loops log progress/elapsed/ETA to BOTH terminal and a file:
    eta = elapsed / done * (total - done)
File lines carry an [HH:MM:SS] wall-clock timestamp; elapsed uses a monotonic clock
(immune to wall-clock jumps). Reused by H2-b (trial) and H2-c (epoch+batch).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable


def _fmt_dur(seconds: float) -> str:
    s = int(round(seconds))
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if h:
        return f"{h}h{m:02d}m"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


class ProgressLogger:
    """Emit `[label k/total] elapsed .. | ETA ~.. | msg` to terminal + optional file."""

    def __init__(self, total: int, label: str, log_path: str | Path | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.total = int(total)
        self.label = label
        self.log_path = Path(log_path) if log_path else None
        self._clock = clock
        self._start = clock()
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"start (total={self.total})")

    def _elapsed(self) -> float:
        return self._clock() - self._start

    def update(self, done: int, msg: str = "") -> str:
        elapsed = self._elapsed()
        if done > 0:
            eta = elapsed / done * (self.total - done)
            eta_s = f"ETA ~{_fmt_dur(eta)}"
        else:
            eta_s = "ETA ~?"
        line = f"[{self.label} {done}/{self.total}] elapsed {_fmt_dur(elapsed)} | {eta_s}"
        if msg:
            line += f" | {msg}"
        self._emit(line)
        return line

    def log(self, msg: str) -> None:
        self._emit(f"[{self.label}] {msg}")

    def done(self, msg: str = "") -> None:
        tail = f" | {msg}" if msg else ""
        self.log(f"done in {_fmt_dur(self._elapsed())}{tail}")

    def _emit(self, line: str) -> None:
        print(line, flush=True)
        if self.log_path:
            ts = time.strftime("[%H:%M:%S]")
            with self.log_path.open("a") as f:
                f.write(f"{ts} {line}\n")
