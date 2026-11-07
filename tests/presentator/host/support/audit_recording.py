"""Files read and processes launched in one thread, recorded by an audit hook."""

import sys
import threading
from dataclasses import dataclass, field

LAUNCH_EVENTS = frozenset(
    {
        "os.fork",
        "os.forkpty",
        "os.posix_spawn",
        "os.spawn",
        "os.exec",
        "os.system",
        "subprocess.Popen",
    }
)
READ_EVENTS = frozenset({"open", "os.scandir", "os.listdir"})


@dataclass
class AuditRecording:
    """One recording; the audit hook is installed once and stays installed."""

    on: bool = False
    thread: int = 0
    events: list[tuple[str, object]] = field(default_factory=list)
    hooked: bool = False

    def record(self, event: str, arguments: tuple[object, ...]) -> None:
        if not self.on or self.thread != threading.get_ident():
            return
        if event in LAUNCH_EVENTS or (event in READ_EVENTS and arguments):
            self.events.append((event, arguments[0] if arguments else None))

    def start(self) -> None:
        if not self.hooked:
            sys.addaudithook(self.record)
            self.hooked = True
        self.events = []
        self.thread = threading.get_ident()
        self.on = True


audit_recording = AuditRecording()
