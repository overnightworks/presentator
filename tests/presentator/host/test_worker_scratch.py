"""The scratch proof: no entry in a scratch place and no SysV object."""

from pathlib import Path

import pytest

from presentator.host.worker_scratch import (
    QUEUE_MOUNT,
    SHARED_MEMORY_MOUNT,
    SYSV_READ_BYTES,
    TEMPORARY_MOUNT,
    WORK_MOUNT,
    read_scratch,
    scratch_empty,
)
from tests.presentator.host.support.sealed_snapshot import SYSV_HEADERS

# Object rows as a kernel lists them to a reader without CAP_IPC_OWNER.
SYSV_OBJECT_ROWS = {
    "shm": (
        "     21297          0   600                  4096     7     0      0"
        " 61001 61001 61001 61001          0          0 1794049611"
        "                     0                     0\n"
    ),
    "msg": (
        "     21298          0   600           0          0     0     0"
        " 61001 61001 61001 61001          0          0 1794049611\n"
    ),
    "sem": (
        "     21299          0   600          1 61001 61001 61001 61001"
        "          0 1794049612\n"
    ),
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    for mount in (WORK_MOUNT, TEMPORARY_MOUNT, SHARED_MEMORY_MOUNT, QUEUE_MOUNT):
        (tmp_path / mount.lstrip("/")).mkdir(parents=True)
    for table, header in SYSV_HEADERS.items():
        path = tmp_path / "proc/sysvipc" / table
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header)
    return tmp_path


def table_path(root: Path, table: str) -> Path:
    return root / "proc/sysvipc" / table


def test_an_empty_tree_is_empty(root: Path) -> None:
    assert scratch_empty(read_scratch(root))


@pytest.mark.parametrize(
    "mount", [WORK_MOUNT, TEMPORARY_MOUNT, SHARED_MEMORY_MOUNT, QUEUE_MOUNT]
)
def test_a_dotfile_in_any_root_is_not_empty(root: Path, mount: str) -> None:
    (root / mount.lstrip("/") / ".residue").touch()

    assert not scratch_empty(read_scratch(root))


def test_a_closed_directory_is_refused_by_its_name_without_opening_it(
    root: Path,
) -> None:
    closed = root / TEMPORARY_MOUNT.lstrip("/") / "closed"
    closed.mkdir()
    (closed / "file").write_text("residue")
    closed.chmod(0)

    try:
        scratch = read_scratch(root)
    finally:
        closed.chmod(0o700)

    assert not scratch_empty(scratch)


@pytest.mark.parametrize("table", SYSV_OBJECT_ROWS)
def test_an_object_row_in_a_table_is_not_empty(root: Path, table: str) -> None:
    with table_path(root, table).open("a") as listing:
        listing.write(SYSV_OBJECT_ROWS[table])

    assert not scratch_empty(read_scratch(root))


@pytest.mark.parametrize("table", SYSV_OBJECT_ROWS)
def test_a_table_longer_than_the_read_is_not_empty(root: Path, table: str) -> None:
    rows = SYSV_OBJECT_ROWS[table] * (SYSV_READ_BYTES // 40)
    table_path(root, table).write_text(SYSV_HEADERS[table] + rows)

    assert not scratch_empty(read_scratch(root))


@pytest.mark.parametrize(
    "content",
    [
        "",
        "garbage\n",
        SYSV_HEADERS["msg"],
        SYSV_HEADERS["shm"].rstrip("\n"),
        SYSV_HEADERS["shm"] + SYSV_HEADERS["shm"],
        SYSV_HEADERS["shm"].replace("key", "id"),
    ],
    ids=[
        "empty",
        "foreign",
        "another table's header",
        "header without its newline",
        "header twice",
        "header without key",
    ],
)
def test_a_shared_memory_table_that_is_not_its_header_line_is_not_empty(
    root: Path, content: str
) -> None:
    table_path(root, "shm").write_text(content)

    assert not scratch_empty(read_scratch(root))


def test_a_missing_table_fails_the_read(root: Path) -> None:
    table_path(root, "sem").unlink()

    with pytest.raises(OSError, match="sem"):
        read_scratch(root)


def test_a_missing_root_fails_the_read(root: Path) -> None:
    (root / QUEUE_MOUNT.lstrip("/")).rmdir()

    with pytest.raises(OSError, match="mqueue"):
        read_scratch(root)


def test_a_table_that_is_not_text_fails_the_read(root: Path) -> None:
    table_path(root, "msg").write_bytes(b"\xff\n")

    with pytest.raises(UnicodeDecodeError):
        read_scratch(root)
