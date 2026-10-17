"""Synthetic research batches for the ADR-026 batch tests.

:func:`batch_front_matter` returns a valid two-candidate front matter as a
plain mapping, which a test edits before :func:`batch_text` renders it. Every
value is made up; no market data is read.
"""

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

EXPERIMENT = "lab"
BATCH_ID = "b1"
BATCH_PATH = f"docs/experiments/{EXPERIMENT}/batches/{BATCH_ID}.md"
PRECURSOR = "a" * 40
CODE_COMMIT = "c" * 40
CARD_BYTES = {
    "swing-alpha": b"# Card: alpha\n",
    "swing-beta": b"# Card: beta\n",
}
BODY = "\n# Batch b1\n\n## C1 — swing-alpha\n\nRules.\n\n## C2 — swing-beta\n\nRules.\n"


def card_path(strategy_id: str) -> str:
    """Where a candidate's card lives."""
    return f"docs/experiments/{EXPERIMENT}/cards/{strategy_id}.md"


def _candidate(strategy_id: str, family: str, section: str) -> dict[str, Any]:
    return {
        "strategy_id": strategy_id,
        "idea_family": family,
        "card": card_path(strategy_id),
        "card_sha256": hashlib.sha256(CARD_BYTES[strategy_id]).hexdigest(),
        "section": section,
        "summary": f"{strategy_id} enters on its signal.\nExpected sign: positive.\n",
        "code_paths": [f"src/hermes/{strategy_id.replace('-', '_')}.py"],
        "parameters": {
            "lookback": {"base": 20, "neighbours": [15, 25]},
            "atr_mult": {"base": "3.0", "neighbours": ["2.5", "3.5"]},
        },
    }


_VALID: dict[str, Any] = {
    "batch_id": BATCH_ID,
    "experiment": EXPERIMENT,
    "status": "SIGNED",
    "signed_by": "Code Owner <owner@example.invalid>",
    "date_signed": "2026-10-18T09:00:00Z",
    "content_sha256": "0" * 64,
    "content_sha256_scope": f"git show {PRECURSOR}:{BATCH_PATH} | sha256sum",
    "signing_commit_precursor": PRECURSOR,
    "code_commit": CODE_COMMIT,
    "selection": {
        "eligible": "full_gate_train_validation",
        "fdr": "benjamini_hochberg",
        "fdr_q": 0.05,
        "rank": "dsr",
        "tie_break": "lowest_mean_return_correlation",
    },
    "backtest_funnel": {
        "dsr_min": 0.95,
        "train_end": "2024-06-30",
        "oos_start": "2025-09-01",
        "cscv_blocks": 16,
        "pbo_max": 0.05,
        "t_stat_above": 3.0,
        "trade_count_min": 100,
        "cost_stress_multiplier": 2.0,
        "positive_year_share_above": 0.5,
        "validation_to_train_return_min": 0.5,
        "neighbour_sharpe_ratio_min": 0.5,
        "return_correlation_below": 0.7,
        "regimes": [
            {"kind": "bull", "start": "2020-10-01", "end": "2021-11-10"},
            {"kind": "bear", "start": "2021-11-11", "end": "2022-11-21"},
        ],
    },
    "candidates": [
        _candidate("swing-alpha", "breakout", "C1"),
        _candidate("swing-beta", "pullback", "C2"),
    ],
}


def batch_front_matter() -> dict[str, Any]:
    """Return a fresh copy of a front matter that passes every check a file shows by itself."""
    return copy.deepcopy(_VALID)


def batch_text(front_matter: Mapping[str, Any] | None = None, body: str = BODY) -> str:
    """Render ``front_matter`` (default: the valid one) and ``body`` as a batch document."""
    rendered = yaml.safe_dump(
        dict(front_matter or batch_front_matter()), sort_keys=False, allow_unicode=True
    )
    return f"---\n{rendered}---\n{body}"


@dataclass
class FakeGit:
    """In-memory :class:`~hermes.ports.git_history.GitHistoryPort`: files per commit."""

    files: dict[str, dict[str, bytes]] = field(default_factory=dict)

    def file_at(self, commit: str, path: str) -> bytes | None:
        """Return the bytes stored for ``path`` at ``commit``."""
        return self.files.get(commit, {}).get(path)

    def object_id(self, commit: str, path: str) -> str | None:
        """Return a content hash standing in for git's object id."""
        data = self.file_at(commit, path)
        return None if data is None else hashlib.sha1(data, usedforsecurity=False).hexdigest()

    def is_ancestor(self, commit: str, of: str) -> bool:
        """Every stored commit counts as an ancestor of ``of``."""
        return commit in self.files and of in self.files


def consistent_git(text: str) -> FakeGit:
    """Return a history where the precursor holds ``text`` and cards and code match the batch."""
    code = {f"src/hermes/{sid.replace('-', '_')}.py": b"code\n" for sid in CARD_BYTES}
    cards = {card_path(sid): data for sid, data in CARD_BYTES.items()}
    return FakeGit(
        files={
            PRECURSOR: {BATCH_PATH: text.encode()},
            CODE_COMMIT: dict(code),
            "HEAD": {**code, **cards},
        }
    )


def signed(front_matter: dict[str, Any], body: str = BODY) -> tuple[str, FakeGit]:
    """Return a SIGNED batch whose ``content_sha256`` is the hash of its precursor version.

    The precursor is the DRAFT of the same front matter, as pre-registration v2 signs.
    """
    draft = copy.deepcopy(front_matter)
    draft.update(
        status="DRAFT",
        signed_by=None,
        date_signed=None,
        content_sha256=None,
        content_sha256_scope=None,
        signing_commit_precursor=None,
    )
    draft_text = batch_text(draft, body)
    front_matter["content_sha256"] = hashlib.sha256(draft_text.encode()).hexdigest()
    git = consistent_git(draft_text)
    return batch_text(front_matter, body), git
