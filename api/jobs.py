"""Background jobs. Only one browser task runs at a time."""

import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterator


class Busy(Exception):
    """Another browser task is already running."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass
class Job:
    id: str
    kind: str                       # "search" or "locations"
    status: str = "running"         # running | done | error
    message: str = "Starting"
    page: int = 0
    count: int = 0
    error: str | None = None
    started_at: str = field(default_factory=now_iso)
    finished_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "status": self.status,
            "message": self.message, "page": self.page, "count": self.count,
            "error": self.error, "startedAt": self.started_at, "finishedAt": self.finished_at,
        }


class Jobs:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._browser = threading.Lock()

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Hold the browser for a short synchronous task."""
        if not self._browser.acquire(blocking=False):
            raise Busy()
        try:
            yield
        finally:
            self._browser.release()

    def start(self, kind: str, work: Callable[[Job], None]) -> Job:
        if not self._browser.acquire(blocking=False):
            raise Busy()
        job = Job(id=uuid.uuid4().hex[:8], kind=kind)
        self._jobs[job.id] = job

        def run() -> None:
            try:
                work(job)
                job.status = "done"
            except Exception as exc:  # report any failure to the UI
                job.status = "error"
                job.error = str(exc) or exc.__class__.__name__
            finally:
                job.finished_at = now_iso()
                self._browser.release()

        threading.Thread(target=run, daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)
