"""Concurrency contract for ``scripts/data/_manifest_util.py``.

Asserts that the read-modify-atomic-write transaction preserves every
mutation across concurrent writers — the bug that motivated change
``data-manifest-file-lock`` (#257).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from threading import Thread

from tests.support.paths import REPO_ROOT


def _import_manifest_util():
    """Import the same-dir helper module used by scripts/data/ ingest scripts."""
    root = REPO_ROOT
    module_path = root / "scripts" / "data" / "_manifest_util.py"
    assert module_path.exists(), f"missing helper module: {module_path}"
    spec = importlib.util.spec_from_file_location("_manifest_util", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["_manifest_util"] = module
    spec.loader.exec_module(module)
    return module


def _writer(path: Path, prefix: str, count: int) -> None:
    """Add ``count`` unique keys prefixed with ``prefix`` under the transaction."""
    module = _import_manifest_util()
    for i in range(count):
        with module.manifest_transaction(path) as m:
            m[f"{prefix}/{i:04d}"] = {"idx": i, "prefix": prefix}


def test_two_threads_no_lost_updates(tmp_path: Path) -> None:
    """Two threads writing 100 unique keys each — final manifest holds all 200.

    Reproduces the race the transaction was introduced to fix: without
    the shared lock, one writer's last flush overwrites the other's
    just-committed entries. With ``manifest_transaction``, every write
    is serialised and each thread's keys survive.
    """
    manifest_path = tmp_path / "data-manifest.json"

    a = Thread(target=_writer, args=(manifest_path, "A", 100))
    b = Thread(target=_writer, args=(manifest_path, "B", 100))
    a.start()
    b.start()
    a.join(timeout=30)
    b.join(timeout=30)
    assert not a.is_alive(), "writer A did not finish within 30s"
    assert not b.is_alive(), "writer B did not finish within 30s"

    data = json.loads(manifest_path.read_text())
    a_keys = {k for k in data if k.startswith("A/")}
    b_keys = {k for k in data if k.startswith("B/")}
    assert len(a_keys) == 100, f"expected 100 A/* keys, got {len(a_keys)}"
    assert len(b_keys) == 100, f"expected 100 B/* keys, got {len(b_keys)}"
    assert len(data) == 200, f"expected 200 total keys, got {len(data)}"


def test_corrupt_file_is_recovered(tmp_path: Path) -> None:
    """A pre-existing corrupt manifest is treated as empty on read."""
    manifest_path = tmp_path / "data-manifest.json"
    manifest_path.write_text("{not-valid-json")
    module = _import_manifest_util()
    with module.manifest_transaction(manifest_path) as m:
        assert m == {}, f"corrupt file should read as empty dict, got {m}"
        m["fresh"] = {"ok": True}
    # Second read should see the newly-written data.
    data = json.loads(manifest_path.read_text())
    assert data == {"fresh": {"ok": True}}, f"expected fresh write to persist, got {data}"


def test_exception_in_block_still_writes(tmp_path: Path) -> None:
    """Mutations made before an exception inside the block are persisted.

    Matches the checkpoint-every-N semantics of the ingest scripts —
    partial progress must not be discarded when a later step fails.
    """
    manifest_path = tmp_path / "data-manifest.json"
    module = _import_manifest_util()
    try:
        with module.manifest_transaction(manifest_path) as m:
            m["saved"] = {"idx": 0}
            raise RuntimeError("simulated mid-block failure")
    except RuntimeError as err:
        assert str(err) == "simulated mid-block failure", f"unexpected err: {err}"
    data = json.loads(manifest_path.read_text())
    assert data == {"saved": {"idx": 0}}, f"partial write should survive exception, got {data}"
