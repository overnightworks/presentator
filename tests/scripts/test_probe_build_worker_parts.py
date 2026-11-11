"""The sealed job's parts drive every cell and every refused upload exactly once."""

import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from ruamel.yaml import YAML

_WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"


@pytest.fixture
def probe(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker")


@pytest.fixture
def uploads(load_script: Callable[..., ModuleType]) -> ModuleType:
    return load_script("probe_build_worker_uploads")


def test_the_parts_drive_every_cell_once(probe: ModuleType) -> None:
    driven = Counter(cell for cells in probe.PARTS.values() for cell in cells)

    assert driven == Counter(probe.CELLS)


@pytest.mark.parametrize("shards", [1, 4])
def test_the_shares_hold_every_upload_row_once(
    uploads: ModuleType, shards: int
) -> None:
    held = Counter(
        name
        for shard in range(1, shards + 1)
        for name in uploads.upload_rows_of_shard(shard, shards)
    )

    assert held == Counter(uploads.UPLOAD_CLASSES.keys())


def test_one_share_of_one_holds_every_row(uploads: ModuleType) -> None:
    assert uploads.upload_rows_of_shard(1, 1) == uploads.UPLOAD_CLASSES


@pytest.mark.parametrize(("shard", "shards"), [(0, 4), (5, 4), (1, 0)])
def test_a_share_outside_its_count_is_refused(
    uploads: ModuleType, shard: int, shards: int
) -> None:
    with pytest.raises(ValueError, match="is not a share"):
        uploads.upload_rows_of_shard(shard, shards)


def test_the_workflow_legs_are_the_parts_and_the_upload_shares(
    probe: ModuleType,
) -> None:
    workflow: dict[str, Any] = YAML(typ="safe").load(  # pyright: ignore[reportUnknownMemberType]
        _WORKFLOW.read_text(encoding="utf-8")
    )
    legs = workflow["jobs"]["build-worker-parts"]["strategy"]["matrix"]["include"]
    upload_legs = [leg for leg in legs if leg["part"] == "uploads"]

    assert {leg["part"] for leg in legs} == {*probe.PARTS, "web"}
    assert [leg["part"] for leg in legs].count("cells") == 1
    assert len({leg["shards"] for leg in upload_legs}) == 1
    assert sorted(leg["shard"] for leg in upload_legs) == list(
        range(1, upload_legs[0]["shards"] + 1)
    )


def test_the_probe_loads_where_pypdf_is_absent(
    monkeypatch: pytest.MonkeyPatch, load_script: Callable[..., ModuleType]
) -> None:
    # The client side runs in the holder container, which has no dev group.
    monkeypatch.setitem(sys.modules, "pypdf", None)

    probe = load_script("probe_build_worker")

    assert callable(probe.client)
