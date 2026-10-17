"""Research batches: one signed document, 2 to 6 candidates (ADR-026 §1).

A batch lives at ``docs/experiments/<experiment>/batches/<batch_id>.md``. Its
YAML front matter names the candidates, the champion ``selection`` rule and
one ``backtest_funnel`` block that binds every candidate. Each candidate's
pre-registration is a section of the body.

:func:`load_batch` parses a batch and refuses one that breaks a rule a file
can show by itself. :func:`check_batch` runs every check, including those
that read history through :class:`~hermes.ports.git_history.GitHistoryPort`
(the signing hash, the card hashes and the bound code), and returns one
:class:`BatchCheck` per check so the operator sees each pass and fail.

Reference: ADR-026 §1, §2; ADR-021 §5; issue #1057.
"""

import hashlib
import math
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Final, Literal

import yaml

from hermes.application.backtest.funnel_replay import (
    BASE_TRIAL_ID,
    COST_STRESS,
    FunnelReplay,
    neighbour,
)
from hermes.application.calibration.backtest_funnel import (
    FunnelThresholdError,
    FunnelThresholds,
    parse_funnel_block,
)
from hermes.ports.git_history import GitHistoryPort

__all__ = [
    "BATCH_CHECK_NAMES",
    "FLOORS",
    "MAX_CANDIDATES",
    "MAX_PARAMETERS",
    "MAX_SUMMARY_LINES",
    "MIN_CANDIDATES",
    "BatchCheck",
    "BatchError",
    "BatchFunnel",
    "Candidate",
    "Floor",
    "Parameter",
    "ResearchBatch",
    "Selection",
    "check_batch",
    "load_batch",
    "parse_batch",
    "sha256_scope",
]

MIN_CANDIDATES: Final = 2
MAX_CANDIDATES: Final = 6
MAX_PARAMETERS: Final = 3
"""ADR-021 stage 1: a hypothesis card frees at most three parameters."""
MAX_SUMMARY_LINES: Final = 6

BATCH_CHECK_NAMES: Final = (
    "front_matter",
    "status_signed",
    "content_sha256",
    "candidate_count",
    "one_per_family",
    "summary_present",
    "section_present",
    "card_hashes",
    "parameters_cap",
    "funnel_floors",
    "selection",
    "strategy_ids_known",
    "code_paths",
)
"""Every check, in output order. ``status_signed`` runs only when a signature is required."""

_TOP_KEYS: Final = frozenset(
    {
        "batch_id",
        "experiment",
        "status",
        "signed_by",
        "date_signed",
        "content_sha256",
        "content_sha256_scope",
        "signing_commit_precursor",
        "code_commit",
        "selection",
        "backtest_funnel",
        "candidates",
    }
)
_OPTIONAL_TOP_KEYS: Final = frozenset({"name", "author", "date_drafted"})
_CANDIDATE_KEYS: Final = frozenset(
    {
        "strategy_id",
        "idea_family",
        "card",
        "card_sha256",
        "section",
        "summary",
        "code_paths",
        "parameters",
    }
)
_SELECTION: Final = {
    "eligible": "full_gate_train_validation",
    "fdr": "benjamini_hochberg",
    "rank": "dsr",
    "tie_break": "lowest_mean_return_correlation",
}
_FDR_Q_MAX: Final = 0.05
_STATUSES: Final = ("DRAFT", "SIGNED")
_BATCH_PATH_PARTS: Final = 5
"""docs / experiments / <experiment> / batches / <batch_id>.md"""

_ID = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PARAMETER = re.compile(r"^[a-z_][a-z0-9_]*$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PRECURSOR = re.compile(r"^[0-9a-f]{7,40}$")

Status = Literal["DRAFT", "SIGNED"]
ParameterValue = int | Decimal


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Floor:
    """One ADR-021 §5 bound a batch's funnel block may tighten but never loosen."""

    key: str
    bound: float
    at_least: bool
    """True: the value must be >= ``bound``; False: <= ``bound``."""

    def holds(self, value: float) -> bool:
        """Return True when ``value`` is at the floor or stricter."""
        return value >= self.bound if self.at_least else value <= self.bound

    def describe(self) -> str:
        """The bound as the refusal states it, such as ``>= 0.95``."""
        return f"{'>=' if self.at_least else '<='} {self.bound:g}"


FLOORS: Final = (
    Floor("dsr_min", 0.95, at_least=True),
    Floor("pbo_max", 0.05, at_least=False),
    Floor("t_stat_above", 3.0, at_least=True),
    Floor("trade_count_min", 100, at_least=True),
)
"""ADR-021 §5 and ADR-026 §1: DSR >= 0.95, PBO <= 0.05, t above 3, at least 100 trades."""


class BatchError(ValueError):
    """A batch broke one check.

    Attributes:
        check: The name of the failed check, one of :data:`BATCH_CHECK_NAMES`.
        message: Why it failed.
    """

    def __init__(self, check: str, message: str) -> None:
        super().__init__(f"{check}: {message}")
        self.check = check
        self.message = message


@dataclass(frozen=True, slots=True)
class BatchCheck:
    """One check of a batch with the reason for its verdict."""

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class Parameter:
    """One free parameter of a candidate: its signed base value and its neighbours."""

    name: str
    base: ParameterValue
    neighbours: tuple[ParameterValue, ...]


@dataclass(frozen=True, slots=True)
class Candidate:
    """One signed candidate (ADR-026 §1)."""

    strategy_id: str
    idea_family: str
    card: str
    card_sha256: str
    section: str
    summary: str
    code_paths: tuple[str, ...]
    parameters: tuple[Parameter, ...]

    def funnel_replays(self) -> tuple[FunnelReplay, ...]:
        """The base run, each neighbour in parameter then value order, then the cost stress."""
        moved = tuple(neighbour(p.name, v) for p in self.parameters for v in p.neighbours)
        return (FunnelReplay(trial_id=BASE_TRIAL_ID), *moved, COST_STRESS)

    def neighbour_trial_ids(self) -> tuple[str, ...]:
        """The trial ids of the neighbour replays, in :meth:`funnel_replays` order."""
        return tuple(r.trial_id for r in self.funnel_replays() if r.overrides)


@dataclass(frozen=True, slots=True)
class Selection:
    """The champion rule of ADR-026 §5; only the false discovery rate q is a free number."""

    fdr_q: float


@dataclass(frozen=True, slots=True)
class BatchFunnel:
    """The batch's ``backtest_funnel`` block: the DSR floor plus the ADR-021 §5 funnel."""

    dsr_min: float
    thresholds: FunnelThresholds


@dataclass(frozen=True, slots=True)
class ResearchBatch:
    """A batch document that passed every check a file can show by itself."""

    path: str
    batch_id: str
    experiment: str
    status: Status
    signed_by: str | None
    date_signed: str | None
    content_sha256: str | None
    content_sha256_scope: str | None
    signing_commit_precursor: str | None
    code_commit: str | None
    selection: Selection
    funnel: BatchFunnel
    candidates: tuple[Candidate, ...]
    """In document order, which is run order."""

    @property
    def trial_log(self) -> str:
        """The repo-relative path of the experiment's trial log."""
        return f"docs/experiments/{self.experiment}/trial-log.jsonl"

    def candidate(self, strategy_id: str) -> Candidate | None:
        """Return the candidate with ``strategy_id``, or None when the batch does not name it."""
        return next((c for c in self.candidates if c.strategy_id == strategy_id), None)


def sha256_scope(precursor: str, path: str) -> str:
    """The one ``content_sha256_scope`` a batch at ``path`` signed from ``precursor`` may state."""
    return f"git show {precursor}:{path} | sha256sum"


# ---------------------------------------------------------------------------
# Field readers
# ---------------------------------------------------------------------------


def _split(text: str) -> tuple[dict[str, object], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise BatchError("front_matter", "no YAML front matter (first line must be ---)")
    end = next((i for i, line in enumerate(lines[1:], start=1) if line.strip() == "---"), None)
    if end is None:
        raise BatchError("front_matter", "YAML front matter is not closed by ---")
    try:
        parsed = yaml.safe_load("".join(lines[1:end]))
    except yaml.YAMLError as exc:
        raise BatchError("front_matter", f"front matter is not valid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise BatchError("front_matter", "front matter must be a mapping")
    return {str(k): v for k, v in parsed.items()}, "".join(lines[end + 1 :])


def _text(check: str, value: object, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchError(check, f"{key} must be non-empty text")
    return value.strip()


def _matching(check: str, value: object, key: str, pattern: re.Pattern[str], shape: str) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise BatchError(check, f"{key} must be {shape}, got {value!r}")
    return value


def _optional(
    check: str, value: object, key: str, pattern: re.Pattern[str], shape: str
) -> str | None:
    return None if value is None else _matching(check, value, key, pattern, shape)


def _repo_path(value: object, key: str) -> str:
    text = _text("front_matter", value, key)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts or text != path.as_posix():
        raise BatchError("front_matter", f"{key} must be a plain repo-relative path, got {text!r}")
    return text


def _value(value: object, key: str) -> ParameterValue:
    """An integer stays an integer; a decimal is a YAML string (contract §1)."""
    if isinstance(value, bool | float):
        raise BatchError("parameters_cap", f"{key} must be an integer or a quoted decimal")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            number = Decimal(value)
        except InvalidOperation:
            pass
        else:
            if number.is_finite():
                return number
    raise BatchError("parameters_cap", f"{key} must be an integer or a quoted decimal")


def _parameters(where: str, value: object) -> tuple[Parameter, ...]:
    if not isinstance(value, Mapping) or not value:
        raise BatchError("parameters_cap", f"{where}.parameters must name at least one parameter")
    if len(value) > MAX_PARAMETERS:
        raise BatchError(
            "parameters_cap",
            f"{where} frees {len(value)} parameters; a card allows at most {MAX_PARAMETERS}",
        )
    out: list[Parameter] = []
    for name, spec in value.items():
        key = f"{where}.parameters.{name}"
        if not isinstance(name, str) or not _PARAMETER.match(name):
            raise BatchError("parameters_cap", f"{key} is not a parameter name")
        if not isinstance(spec, Mapping) or set(spec) != {"base", "neighbours"}:
            raise BatchError("parameters_cap", f"{key} needs exactly base and neighbours")
        base = _value(spec["base"], f"{key}.base")
        raw = spec["neighbours"]
        if not isinstance(raw, list) or not raw:
            raise BatchError("parameters_cap", f"{key}.neighbours must be a non-empty list")
        neighbours = tuple(_value(v, f"{key}.neighbours[{i}]") for i, v in enumerate(raw))
        for i, n in enumerate(neighbours):
            if type(n) is not type(base):
                raise BatchError(
                    "parameters_cap", f"{key}.neighbours[{i}] is not the same kind as base"
                )
            if n == base:
                raise BatchError("parameters_cap", f"{key}.neighbours[{i}] equals base")
        if len(set(neighbours)) != len(neighbours):
            raise BatchError("parameters_cap", f"{key}.neighbours repeats a value")
        out.append(Parameter(name, base, neighbours))
    return tuple(out)


def _summary(where: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BatchError("summary_present", f"{where} has no summary")
    lines = value.strip().splitlines()
    if len(lines) > MAX_SUMMARY_LINES:
        raise BatchError(
            "summary_present",
            f"{where}.summary has {len(lines)} lines; at most {MAX_SUMMARY_LINES}",
        )
    return value.strip()


def _keys(
    where: str, present: Collection[str], required: frozenset[str], optional: frozenset[str]
) -> None:
    missing = sorted(required - set(present))
    unknown = sorted(set(present) - required - optional)
    if missing or unknown:
        raise BatchError("front_matter", f"{where} keys: missing {missing}, unknown {unknown}")


@dataclass(frozen=True, slots=True)
class _RawCandidate:
    index: int
    fields: Mapping[str, object]

    @property
    def where(self) -> str:
        return f"candidates[{self.index}]"


def _raw_candidates(front_matter: Mapping[str, object]) -> tuple[_RawCandidate, ...]:
    value = front_matter.get("candidates")
    if not isinstance(value, list):
        raise BatchError("front_matter", "candidates must be a list")
    out: list[_RawCandidate] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise BatchError("front_matter", f"candidates[{index}] must be a mapping")
        raw = _RawCandidate(index, {str(k): v for k, v in item.items()})
        _keys(raw.where, raw.fields, _CANDIDATE_KEYS, frozenset())
        out.append(raw)
    return tuple(out)


def _identity(raw: _RawCandidate) -> tuple[str, str]:
    where = raw.where
    strategy_id = _matching(
        "front_matter", raw.fields["strategy_id"], f"{where}.strategy_id", _ID, "a lowercase id"
    )
    family = _matching(
        "front_matter", raw.fields["idea_family"], f"{where}.idea_family", _ID, "a lowercase id"
    )
    return strategy_id, family


def _code_paths(where: str, value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise BatchError("front_matter", f"{where}.code_paths must be a non-empty list")
    return tuple(_repo_path(p, f"{where}.code_paths[{i}]") for i, p in enumerate(value))


def _candidate(raw: _RawCandidate) -> Candidate:
    where = raw.where
    strategy_id, family = _identity(raw)
    return Candidate(
        strategy_id=strategy_id,
        idea_family=family,
        card=_repo_path(raw.fields["card"], f"{where}.card"),
        card_sha256=_matching(
            "front_matter",
            raw.fields["card_sha256"],
            f"{where}.card_sha256",
            _SHA256,
            "64 lowercase hex digits",
        ),
        section=_text("front_matter", raw.fields["section"], f"{where}.section"),
        summary=_summary(where, raw.fields["summary"]),
        code_paths=_code_paths(where, raw.fields["code_paths"]),
        parameters=_parameters(where, raw.fields["parameters"]),
    )


# ---------------------------------------------------------------------------
# Checks a file shows by itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Header:
    batch_id: str
    experiment: str
    status: Status
    candidates: tuple[_RawCandidate, ...]


def _header(path: str, front_matter: Mapping[str, object]) -> _Header:
    _keys("front matter", front_matter, _TOP_KEYS, _OPTIONAL_TOP_KEYS)
    batch_id = _matching(
        "front_matter", front_matter["batch_id"], "batch_id", _ID, "a lowercase id"
    )
    experiment = _matching(
        "front_matter", front_matter["experiment"], "experiment", _ID, "a lowercase id"
    )
    parts = PurePosixPath(path).parts[-_BATCH_PATH_PARTS:]
    expected = ("docs", "experiments", experiment, "batches", f"{batch_id}.md")
    if parts != expected:
        raise BatchError("front_matter", f"{path} is not {'/'.join(expected)}")
    status = front_matter["status"]
    if status not in _STATUSES:
        raise BatchError("front_matter", f"status must be DRAFT or SIGNED, got {status!r}")
    raws = _raw_candidates(front_matter)
    ids = [_identity(r)[0] for r in raws]
    if repeated := sorted({i for i in ids if ids.count(i) > 1}):
        raise BatchError("front_matter", f"strategy ids named twice: {repeated}")
    return _Header(batch_id, experiment, "SIGNED" if status == "SIGNED" else "DRAFT", raws)


def _candidate_count(header: _Header) -> str:
    n = len(header.candidates)
    if not MIN_CANDIDATES <= n <= MAX_CANDIDATES:
        raise BatchError(
            "candidate_count", f"{n} candidates; a batch holds {MIN_CANDIDATES} to {MAX_CANDIDATES}"
        )
    return f"{n} candidates"


def _one_per_family(header: _Header) -> str:
    families = [_identity(r)[1] for r in header.candidates]
    if repeated := sorted({f for f in families if families.count(f) > 1}):
        raise BatchError(
            "one_per_family", f"idea families with more than one candidate: {repeated}"
        )
    return ", ".join(families)


def _summary_present(header: _Header) -> str:
    for raw in header.candidates:
        _summary(raw.where, raw.fields["summary"])
    return "every candidate has a summary"


def _headings(body: str) -> list[str]:
    return [
        line.lstrip("#").strip()
        for line in body.splitlines()
        if line.startswith("## ") or line.startswith("### ")
    ]


def _section_present(header: _Header, body: str) -> str:
    headings = _headings(body)
    for raw in header.candidates:
        section = _text("section_present", raw.fields["section"], f"{raw.where}.section")
        found = [h for h in headings if h == section or h.startswith(f"{section} ")]
        if len(found) != 1:
            raise BatchError(
                "section_present",
                f"{raw.where}: {len(found)} headings start with {section!r}, not 1",
            )
    return "every candidate's pre-registration section is in the body"


def _parameters_cap(header: _Header) -> str:
    counts = [len(_parameters(r.where, r.fields["parameters"])) for r in header.candidates]
    return f"free parameters per candidate: {counts}"


def _funnel(front_matter: Mapping[str, object]) -> BatchFunnel:
    block = front_matter["backtest_funnel"]
    if not isinstance(block, Mapping):
        raise BatchError("funnel_floors", "backtest_funnel must be a mapping")
    dsr = block.get("dsr_min")
    if isinstance(dsr, bool) or not isinstance(dsr, int | float) or not math.isfinite(dsr):
        raise BatchError("funnel_floors", f"backtest_funnel.dsr_min must be a number, got {dsr!r}")
    if not 0.0 < dsr < 1.0:
        raise BatchError(
            "funnel_floors", f"backtest_funnel.dsr_min must be above 0 and below 1, got {dsr:g}"
        )
    try:
        thresholds = parse_funnel_block({str(k): v for k, v in block.items() if k != "dsr_min"})
    except FunnelThresholdError as exc:
        raise BatchError("funnel_floors", str(exc)) from exc
    values = {"dsr_min": float(dsr)} | {
        f.key: float(getattr(thresholds, f.key)) for f in FLOORS if f.key != "dsr_min"
    }
    loose = [
        f"{f.key} {values[f.key]:g} (ADR-021 §5: {f.describe()})"
        for f in FLOORS
        if not f.holds(values[f.key])
    ]
    if loose:
        raise BatchError("funnel_floors", "looser than the floors: " + "; ".join(loose))
    return BatchFunnel(dsr_min=float(dsr), thresholds=thresholds)


def _selection(front_matter: Mapping[str, object]) -> Selection:
    block = front_matter["selection"]
    if not isinstance(block, Mapping):
        raise BatchError("selection", "selection must be a mapping")
    expected = {*_SELECTION, "fdr_q"}
    if set(block) != expected:
        raise BatchError(
            "selection", f"selection keys must be {sorted(expected)}, got {sorted(map(str, block))}"
        )
    for key, value in _SELECTION.items():
        if block[key] != value:
            raise BatchError(
                "selection", f"selection.{key} must be {value!r} (ADR-026 §5), got {block[key]!r}"
            )
    q = block["fdr_q"]
    if isinstance(q, bool) or not isinstance(q, int | float) or not 0.0 < q <= _FDR_Q_MAX:
        raise BatchError(
            "selection", f"selection.fdr_q must be above 0 and at most {_FDR_Q_MAX:g}, got {q!r}"
        )
    return Selection(fdr_q=float(q))


def _signature(front_matter: Mapping[str, object], status: Status) -> tuple[str | None, ...]:
    """signed_by, date_signed, content_sha256, scope, precursor and code_commit, shape-checked."""
    fm = front_matter
    signed_by = fm["signed_by"]
    date_signed = fm["date_signed"]
    if status == "SIGNED":
        _text("status_signed", signed_by, "signed_by")
        if date_signed in (None, ""):
            raise BatchError("status_signed", "date_signed is empty")
    return (
        None if signed_by is None else str(signed_by),
        None if date_signed is None else str(date_signed),
        _optional("content_sha256", fm["content_sha256"], "content_sha256", _SHA256, "64 hex"),
        None if fm["content_sha256_scope"] is None else str(fm["content_sha256_scope"]),
        _optional(
            "content_sha256",
            fm["signing_commit_precursor"],
            "signing_commit_precursor",
            _PRECURSOR,
            "a commit",
        ),
        _optional("code_paths", fm["code_commit"], "code_commit", _COMMIT, "a 40-hex commit"),
    )


def parse_batch(path: str, text: str) -> ResearchBatch:
    """Parse a batch's text and refuse it at the first rule the file breaks by itself.

    Args:
        path: The batch's repo-relative POSIX path.
        text: The file's content.

    Returns:
        The typed batch. History checks (hashes, code) are :func:`check_batch`'s.

    Raises:
        BatchError: The first failed check, named.
    """
    front_matter, body = _split(text)
    header = _header(path, front_matter)
    _candidate_count(header)
    _one_per_family(header)
    _summary_present(header)
    _section_present(header, body)
    _parameters_cap(header)
    funnel = _funnel(front_matter)
    selection = _selection(front_matter)
    signed_by, date_signed, digest, scope, precursor, code_commit = _signature(
        front_matter, header.status
    )
    return ResearchBatch(
        path=path,
        batch_id=header.batch_id,
        experiment=header.experiment,
        status=header.status,
        signed_by=signed_by,
        date_signed=date_signed,
        content_sha256=digest,
        content_sha256_scope=scope,
        signing_commit_precursor=precursor,
        code_commit=code_commit,
        selection=selection,
        funnel=funnel,
        candidates=tuple(_candidate(r) for r in header.candidates),
    )


def load_batch(path: Path, *, repo: Path | None = None) -> ResearchBatch:
    """Read and parse the batch at ``path``.

    Args:
        path: The batch file.
        repo: The clone that holds it, to name it by its repo-relative path;
            defaults to the current directory.

    Raises:
        BatchError: The file cannot be read (check ``front_matter``) or breaks a rule.
    """
    root = (repo or Path()).resolve()
    try:
        text = path.read_text(encoding="utf-8")
        relative = path.resolve().relative_to(root).as_posix()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise BatchError("front_matter", f"cannot read {path}: {exc}") from exc
    return parse_batch(relative, text)


# ---------------------------------------------------------------------------
# Checks that read history
# ---------------------------------------------------------------------------


def _content_sha256(batch: ResearchBatch, git: GitHistoryPort, head: str) -> str:
    if batch.status == "DRAFT" and batch.content_sha256 is None:
        return "draft: not hashed yet"
    digest, precursor = batch.content_sha256, batch.signing_commit_precursor
    if digest is None or precursor is None:
        raise BatchError(
            "content_sha256", "content_sha256 and signing_commit_precursor are required"
        )
    if batch.content_sha256_scope != sha256_scope(precursor, batch.path):
        raise BatchError(
            "content_sha256",
            f"content_sha256_scope must be {sha256_scope(precursor, batch.path)!r}",
        )
    if not git.is_ancestor(precursor, head):
        raise BatchError("content_sha256", f"precursor {precursor} is not in this history")
    signed = git.file_at(precursor, batch.path)
    if signed is None:
        raise BatchError("content_sha256", f"{batch.path} does not exist at {precursor}")
    actual = hashlib.sha256(signed).hexdigest()
    if actual != digest:
        raise BatchError(
            "content_sha256", f"{batch.path} at {precursor} hashes to {actual}, not {digest}"
        )
    return f"{digest} matches {batch.path} at {precursor}"


def _card_hashes(batch: ResearchBatch, git: GitHistoryPort, head: str) -> str:
    for c in batch.candidates:
        card = git.file_at(head, c.card)
        if card is None:
            raise BatchError("card_hashes", f"{c.strategy_id}: card {c.card} is not committed")
        actual = hashlib.sha256(card).hexdigest()
        if actual != c.card_sha256:
            raise BatchError(
                "card_hashes", f"{c.strategy_id}: {c.card} hashes to {actual}, not {c.card_sha256}"
            )
    return "every card matches its hash"


def _strategy_ids_known(batch: ResearchBatch, known: Collection[str]) -> str:
    unknown = [c.strategy_id for c in batch.candidates if c.strategy_id not in known]
    if unknown:
        raise BatchError("strategy_ids_known", f"not in config/strategies.yaml: {unknown}")
    return ", ".join(c.strategy_id for c in batch.candidates)


def _code_bound(batch: ResearchBatch, git: GitHistoryPort, head: str) -> str:
    commit = batch.code_commit
    if commit is None:
        if batch.status == "DRAFT":
            return "draft: no code_commit yet"
        raise BatchError("code_paths", "code_commit is required")
    if not git.is_ancestor(commit, head):
        raise BatchError("code_paths", f"code_commit {commit} is not in this history")
    for c in batch.candidates:
        for path in c.code_paths:
            bound = git.object_id(commit, path)
            if bound is None:
                raise BatchError(
                    "code_paths", f"{c.strategy_id}: {path} does not exist at {commit}"
                )
            if git.object_id(head, path) != bound:
                raise BatchError("code_paths", f"{c.strategy_id}: {path} differs from {commit}")
    return f"every candidate's code is as at {commit}"


def _run(name: str, step: Callable[[], str]) -> BatchCheck:
    try:
        return BatchCheck(name, True, step())
    except BatchError as exc:
        detail = exc.message if exc.check == name else f"not checkable: {exc}"
        return BatchCheck(name, False, detail)


def check_batch(
    path: str,
    text: str,
    git: GitHistoryPort,
    *,
    known_strategies: Collection[str],
    require_signed: bool = False,
    head: str = "HEAD",
) -> tuple[BatchCheck, ...]:
    """Run every check, in :data:`BATCH_CHECK_NAMES` order.

    Args:
        path: The batch's repo-relative POSIX path.
        text: The file's content.
        git: History of the clone that holds it.
        known_strategies: Every name in ``config/strategies.yaml``.
        require_signed: Run ``status_signed``; a DRAFT batch then fails.
        head: The commit cards and code are read at.

    Returns:
        One check per name (``status_signed`` only when required). When the
        front matter cannot be read, every later check fails as not checkable.
    """
    try:
        front_matter, body = _split(text)
        header = _header(path, front_matter)
    except BatchError as exc:
        reason = f"not checkable: {exc.message}"
        return (
            BatchCheck("front_matter", False, exc.message),
            *(
                BatchCheck(name, False, reason)
                for name in BATCH_CHECK_NAMES[1:]
                if require_signed or name != "status_signed"
            ),
        )

    def whole_batch() -> ResearchBatch:
        return parse_batch(path, text)

    def signed() -> str:
        if header.status != "SIGNED":
            raise BatchError("status_signed", "status is DRAFT; the run needs a SIGNED batch")
        _signature(front_matter, header.status)
        return f"signed by {front_matter['signed_by']}"

    def after_parse(step: Callable[[ResearchBatch], str]) -> Callable[[], str]:
        return lambda: step(whole_batch())

    checks: list[tuple[str, Callable[[], str]]] = [
        ("front_matter", lambda: f"batch {header.batch_id} of {header.experiment}"),
        ("status_signed", signed),
        ("content_sha256", after_parse(lambda b: _content_sha256(b, git, head))),
        ("candidate_count", lambda: _candidate_count(header)),
        ("one_per_family", lambda: _one_per_family(header)),
        ("summary_present", lambda: _summary_present(header)),
        ("section_present", lambda: _section_present(header, body)),
        ("card_hashes", after_parse(lambda b: _card_hashes(b, git, head))),
        ("parameters_cap", lambda: _parameters_cap(header)),
        ("funnel_floors", lambda: _funnel_described(front_matter)),
        ("selection", lambda: _selection_described(front_matter)),
        ("strategy_ids_known", after_parse(lambda b: _strategy_ids_known(b, known_strategies))),
        ("code_paths", after_parse(lambda b: _code_bound(b, git, head))),
    ]
    return tuple(
        _run(name, step) for name, step in checks if require_signed or name != "status_signed"
    )


def _funnel_described(front_matter: Mapping[str, object]) -> str:
    funnel = _funnel(front_matter)
    return f"dsr_min {funnel.dsr_min:g}; every ADR-021 §5 floor held"


def _selection_described(front_matter: Mapping[str, object]) -> str:
    return f"Benjamini-Hochberg q {_selection(front_matter).fdr_q:g}, highest DSR wins"
