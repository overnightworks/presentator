"""The identity of a toolchain project: what moves it and what leaves it alone."""

import os
import re
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Self

import pytest

from presentator.adapters import toolchain_identity
from presentator.adapters.toolchain_identity import toolchain_identity_of
from presentator.contracts.decks import ToolchainIdentity
from tests.presentator.adapters.support.build_support import (
    builds_under,
    on_this_machine,
)

_A_PROJECT = {
    "package.json": '{"name": "toolchain"}',
    "pnpm-lock.yaml": "lockfileVersion: 9",
    "tsconfig.json": "{}",
    "components/CoPresenter.vue": "<template />",
    "eslint.config.js": "export default []",
    "vitest.config.ts": "export default {}",
    "tests/overlay.test.ts": "test('overlay')",
}


def a_project(root: Path, files: dict[str, str]) -> Path:
    for name, content in files.items():
        written = root / name
        written.parent.mkdir(parents=True, exist_ok=True)
        written.write_text(content, encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("changes", "moves"),
    [
        pytest.param({}, False, id="the same project"),
        pytest.param(
            {"components/CoPresenter.vue": "<template>x</template>"},
            True,
            id="an overlay file",
        ),
        pytest.param(
            {"pnpm-lock.yaml": "lockfileVersion: 10"}, True, id="the lockfile"
        ),
        pytest.param({"tsconfig.json": '{"strict": true}'}, True, id="tsconfig"),
        pytest.param({"components/Another.vue": "<template />"}, True, id="a new file"),
        pytest.param({"nested/tests/a.ts": "a"}, True, id="a nested tests directory"),
        pytest.param(
            {"nested/eslint.config.js": "a"}, True, id="a nested lint configuration"
        ),
        pytest.param({"tests/overlay.test.ts": "test('changed')"}, False, id="a test"),
        pytest.param({"tests/deeper/new.test.ts": "a"}, False, id="a new test"),
        pytest.param(
            {"eslint.config.js": "export default [1]"},
            False,
            id="the lint configuration",
        ),
        pytest.param(
            {"vitest.config.ts": "export default {1}"},
            False,
            id="the test configuration",
        ),
        pytest.param({"node_modules/dep/index.js": "a"}, False, id="its dependencies"),
        pytest.param(
            {"components/node_modules/dep/index.js": "a"},
            False,
            id="nested dependencies",
        ),
    ],
)
def test_the_identity_moves_with_what_a_build_reads_and_not_with_the_rest(
    tmp_path: Path, changes: dict[str, str], *, moves: bool
) -> None:
    before = toolchain_identity_of(a_project(tmp_path / "before", _A_PROJECT))

    after = toolchain_identity_of(
        a_project(tmp_path / "after", {**_A_PROJECT, **changes})
    )

    assert (after != before) is moves


def test_a_project_is_the_same_wherever_it_stands(tmp_path: Path) -> None:
    here = a_project(tmp_path / "here", _A_PROJECT)
    there = a_project(tmp_path / "somewhere" / "else", _A_PROJECT)

    assert toolchain_identity_of(here) == toolchain_identity_of(there)


@pytest.mark.parametrize(
    "target_is_a_directory", [False, True], ids=["file", "directory"]
)
def test_a_symbolic_link_is_left_out_and_never_followed(
    tmp_path: Path, *, target_is_a_directory: bool
) -> None:
    plain = a_project(tmp_path / "plain", _A_PROJECT)
    linked = a_project(tmp_path / "linked", _A_PROJECT)
    outside = tmp_path / "outside"
    a_project(outside, {"secret.txt": "not part of the project"})
    target = outside if target_is_a_directory else outside / "secret.txt"
    (linked / "components" / "link").symlink_to(target)

    assert toolchain_identity_of(linked) == toolchain_identity_of(plain)


def test_an_entry_that_is_no_regular_file_is_left_out(tmp_path: Path) -> None:
    plain = a_project(tmp_path / "plain", _A_PROJECT)
    with_a_socket = a_project(tmp_path / "with-a-socket", _A_PROJECT)
    os.mknod(with_a_socket / "components" / "socket", stat.S_IFSOCK | 0o600)

    assert toolchain_identity_of(with_a_socket) == toolchain_identity_of(plain)


def test_where_one_file_ends_and_the_next_begins_is_part_of_the_identity(
    tmp_path: Path,
) -> None:
    split_one_way = a_project(tmp_path / "one", {"a": "bc"})
    split_another = a_project(tmp_path / "two", {"ab": "c"})

    assert toolchain_identity_of(split_one_way) != toolchain_identity_of(split_another)


def test_the_identity_is_sixty_four_lowercase_hex_digits(tmp_path: Path) -> None:
    identity = toolchain_identity_of(a_project(tmp_path, _A_PROJECT))

    assert re.fullmatch(r"[0-9a-f]{64}", identity.digest)


@pytest.mark.parametrize(
    "unreadable",
    ["missing", "a file, not a project"],
)
def test_a_project_that_cannot_be_read_is_refused(
    tmp_path: Path, unreadable: str
) -> None:
    project = tmp_path / "project"
    if unreadable != "missing":
        project.write_text("not a directory", encoding="utf-8")

    with pytest.raises(OSError, match=re.escape(str(project))):
        toolchain_identity_of(project)


@pytest.mark.parametrize(
    "digest",
    ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, " " + "a" * 63],
    ids=["empty", "short", "long", "uppercase", "not hex", "padded"],
)
def test_an_identity_refuses_anything_but_sixty_four_lowercase_hex_digits(
    digest: str,
) -> None:
    with pytest.raises(ValueError, match="64 lowercase hex"):
        ToolchainIdentity(digest)


def test_a_file_renamed_with_the_same_content_moves_the_identity(
    tmp_path: Path,
) -> None:
    before = a_project(tmp_path / "before", {"components/A.vue": "<template />"})
    after = a_project(tmp_path / "after", {"components/B.vue": "<template />"})

    assert toolchain_identity_of(before) != toolchain_identity_of(after)


def test_a_file_name_that_is_not_utf8_still_has_an_identity(tmp_path: Path) -> None:
    project = a_project(tmp_path / "project", _A_PROJECT)
    without = toolchain_identity_of(project)
    Path(os.fsdecode(os.fsencode(project) + b"/components/caf\xe9.vue")).write_bytes(
        b"<template />"
    )

    with_it = toolchain_identity_of(project)

    assert with_it == toolchain_identity_of(project)
    assert with_it != without


def test_the_identity_does_not_depend_on_the_order_the_directory_lists_its_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = a_project(tmp_path, {"a.txt": "1", "b.txt": "2", "c/d.txt": "3"})
    listed_in_the_usual_order = toolchain_identity_of(project)
    real_scandir = os.scandir

    class ListsBackwards:
        def __init__(self, path: Path) -> None:
            self._entries = list(real_scandir(path))[::-1]

        def __enter__(self) -> Self:
            return self

        def __iter__(self) -> Iterator[os.DirEntry[str]]:
            return iter(self._entries)

        def __exit__(self, *exception: object) -> None:
            return None

    monkeypatch.setattr(toolchain_identity.os, "scandir", ListsBackwards)

    assert toolchain_identity_of(project) == listed_in_the_usual_order


def test_the_runner_answers_the_identity_it_was_handed(tmp_path: Path) -> None:
    handed = ToolchainIdentity("c" * 64)
    builds = builds_under(
        tmp_path, toolchain=on_this_machine(tmp_path), identity=handed
    )

    assert builds.toolchain_identity() == handed
