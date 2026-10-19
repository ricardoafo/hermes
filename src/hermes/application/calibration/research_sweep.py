"""Research sweeps: one signed batch, many candidates from signed family grids (ADR-031 §1).

A sweep is a research batch whose front matter says ``kind: sweep``. Instead of
hand-written candidates it names up to :data:`MAX_FAMILIES` idea families, each
a strategy template from ``config/strategies.yaml`` with its card, its bar
interval and a ``grid`` of the values each free parameter takes, plus a signed
``max_candidates`` of at most :data:`MAX_SWEEP_CANDIDATES`.

:func:`expand` turns the families into candidates in one fixed order: families
in document order, then the product of each family's grid with its axes in
document order and the last axis changing fastest. A candidate's id is
``<family>@<param>=<value>,...`` with the parameters in axis order. Every
candidate of a family shares its family's card, section and code, so sizing,
fees, universe, exits and kill rules are the same for every point.

:func:`parse_sweep` refuses a sweep at the first rule the file breaks by
itself; :func:`check_sweep` runs every check, history included, and returns one
:class:`~hermes.application.calibration.research_batch.BatchCheck` per check.
The checks a sweep shares with a batch reuse that module's readers, so the
signing hash and the funnel floors mean the same thing in both.

Reference: ADR-031 §1, §6; ADR-026 §1; issue #1309.
"""

import itertools
import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from pathlib import PurePosixPath
from typing import Final

from hermes.application.calibration.research_batch import (
    _BATCH_PATH_PARTS,
    _ID,
    _PARAMETER,
    _SHA256,
    _STATUSES,
    MAX_PARAMETERS,
    MIN_CANDIDATES,
    BatchCheck,
    BatchError,
    Candidate,
    Parameter,
    ParameterValue,
    ResearchBatch,
    Status,
    _card_hashes,
    _code_bound,
    _code_paths,
    _content_sha256,
    _funnel,
    _funnel_described,
    _headings,
    _keys,
    _matching,
    _repo_path,
    _run,
    _selection,
    _selection_described,
    _signature,
    _split,
    _summary,
    _text,
    _value,
)
from hermes.ports.git_history import GitHistoryPort

__all__ = [
    "INTERVALS",
    "MAX_FAMILIES",
    "MAX_SWEEP_CANDIDATES",
    "SWEEP_CHECK_NAMES",
    "Family",
    "GridAxis",
    "SweepBatch",
    "SweepPoint",
    "candidate_id",
    "check_sweep",
    "expand",
    "is_sweep",
    "parse_sweep",
]

MAX_FAMILIES: Final = 12
MAX_SWEEP_CANDIDATES: Final = 150
INTERVALS: Final = ("4h",)
"""Bar intervals a family may name. ``1h`` joins once issues #1237 and #1238 land (ADR-031 §6)."""
_WAITING_INTERVALS: Final = {"1h": "issues #1237 and #1238 (ADR-031 §6, ADR-029)"}
_MIN_GRID_VALUES: Final = 2

SWEEP_CHECK_NAMES: Final = (
    "front_matter",
    "status_signed",
    "content_sha256",
    "family_count",
    "interval_allowed",
    "summary_present",
    "section_present",
    "card_hashes",
    "parameters_cap",
    "candidate_cap",
    "funnel_floors",
    "selection",
    "templates_known",
    "code_paths",
)
"""Every sweep check, in output order. ``status_signed`` runs only when a signature is required."""

_TOP_KEYS: Final = frozenset(
    {
        "batch_id",
        "experiment",
        "kind",
        "status",
        "signed_by",
        "date_signed",
        "content_sha256",
        "content_sha256_scope",
        "signing_commit_precursor",
        "code_commit",
        "selection",
        "backtest_funnel",
        "max_candidates",
        "families",
    }
)
_OPTIONAL_TOP_KEYS: Final = frozenset({"name", "author", "date_drafted"})
_FAMILY_KEYS: Final = frozenset(
    {
        "family",
        "template",
        "card",
        "card_sha256",
        "section",
        "summary",
        "code_paths",
        "interval",
        "grid",
    }
)
_TEMPLATE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PLAIN_NUMBER = re.compile(r"^-?[0-9]+(\.[0-9]+)?$")


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GridAxis:
    """One free parameter of a family and the values the sweep tries, in ascending order."""

    name: str
    values: tuple[ParameterValue, ...]


@dataclass(frozen=True, slots=True)
class Family:
    """One signed idea family of a sweep: a template, its card and its grid (ADR-031 §1)."""

    family: str
    template: str
    card: str
    card_sha256: str
    section: str
    summary: str
    code_paths: tuple[str, ...]
    interval: str
    grid: tuple[GridAxis, ...]


@dataclass(frozen=True, slots=True)
class SweepPoint:
    """One grid point: the candidate id, its family's template and the parameter values."""

    candidate_id: str
    family: str
    template: str
    values: tuple[tuple[str, ParameterValue], ...]


@dataclass(frozen=True, slots=True)
class SweepBatch:
    """A sweep that passed every check a file can show by itself.

    ``batch`` holds every candidate in expansion order, which is run order, so
    the stages that judge a batch judge a sweep the same way. Each candidate's
    neighbours are the adjacent values on its family's grid.
    """

    batch: ResearchBatch
    max_candidates: int
    families: tuple[Family, ...]
    points: tuple[SweepPoint, ...]


# ---------------------------------------------------------------------------
# Expansion
# ---------------------------------------------------------------------------


def _token(value: ParameterValue) -> str:
    return str(value)


def candidate_id(family: str, values: tuple[tuple[str, ParameterValue], ...]) -> str:
    """The generated id of one grid point: ``<family>@<param>=<value>,...``."""
    return f"{family}@" + ",".join(f"{name}={_token(value)}" for name, value in values)


def expand(families: tuple[Family, ...]) -> tuple[SweepPoint, ...]:
    """Every grid point of every family, in the one order a sweep runs.

    Families keep document order; within a family the axes keep document order
    and the last axis changes fastest.
    """
    points: list[SweepPoint] = []
    for f in families:
        names = [axis.name for axis in f.grid]
        for combo in itertools.product(*(axis.values for axis in f.grid)):
            values = tuple(zip(names, combo, strict=True))
            points.append(SweepPoint(candidate_id(f.family, values), f.family, f.template, values))
    return tuple(points)


def _neighbours(axis: GridAxis, value: ParameterValue) -> tuple[ParameterValue, ...]:
    i = axis.values.index(value)
    return axis.values[max(i - 1, 0) : i] + axis.values[i + 1 : i + 2]


def _candidate(f: Family, point: SweepPoint) -> Candidate:
    axes = {axis.name: axis for axis in f.grid}
    return Candidate(
        strategy_id=point.candidate_id,
        idea_family=f.family,
        card=f.card,
        card_sha256=f.card_sha256,
        section=f.section,
        summary=f.summary,
        code_paths=f.code_paths,
        parameters=tuple(
            Parameter(name, value, _neighbours(axes[name], value)) for name, value in point.values
        ),
    )


# ---------------------------------------------------------------------------
# Field readers
# ---------------------------------------------------------------------------


def _grid(where: str, value: object) -> tuple[GridAxis, ...]:
    if not isinstance(value, Mapping) or not value:
        raise BatchError("parameters_cap", f"{where}.grid must name at least one parameter")
    if len(value) > MAX_PARAMETERS:
        raise BatchError(
            "parameters_cap",
            f"{where} frees {len(value)} parameters; a card allows at most {MAX_PARAMETERS}",
        )
    axes: list[GridAxis] = []
    for name, raw in value.items():
        key = f"{where}.grid.{name}"
        if not isinstance(name, str) or not _PARAMETER.match(name):
            raise BatchError("parameters_cap", f"{key} is not a parameter name")
        if not isinstance(raw, list) or len(raw) < _MIN_GRID_VALUES:
            raise BatchError(
                "parameters_cap", f"{key} must list at least {_MIN_GRID_VALUES} values"
            )
        values = tuple(_value(v, f"{key}[{i}]") for i, v in enumerate(raw))
        for i, v in enumerate(values):
            if type(v) is not type(values[0]):
                raise BatchError("parameters_cap", f"{key}[{i}] is not the same kind as {key}[0]")
            if isinstance(v, Decimal) and not _PLAIN_NUMBER.match(raw[i].strip()):
                raise BatchError("parameters_cap", f"{key}[{i}] must be a plain decimal like 2.5")
        if any(b <= a for a, b in itertools.pairwise(values)):
            raise BatchError("parameters_cap", f"{key} must list distinct values, ascending")
        axes.append(GridAxis(name, values))
    return tuple(axes)


@dataclass(frozen=True, slots=True)
class _RawFamily:
    index: int
    fields: Mapping[str, object]

    @property
    def where(self) -> str:
        return f"families[{self.index}]"

    @property
    def name(self) -> str:
        return _matching(
            "front_matter", self.fields["family"], f"{self.where}.family", _ID, "a lowercase id"
        )


def _raw_families(front_matter: Mapping[str, object]) -> tuple[_RawFamily, ...]:
    value = front_matter.get("families")
    if not isinstance(value, list) or not value:
        raise BatchError("front_matter", "families must be a non-empty list")
    out: list[_RawFamily] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise BatchError("front_matter", f"families[{index}] must be a mapping")
        raw = _RawFamily(index, {str(k): v for k, v in item.items()})
        _keys(raw.where, raw.fields, _FAMILY_KEYS, frozenset())
        out.append(raw)
    return tuple(out)


def _interval(raw: _RawFamily) -> str:
    value = raw.fields["interval"]
    if value in INTERVALS:
        return str(value)
    if isinstance(value, str) and value in _WAITING_INTERVALS:
        raise BatchError(
            "interval_allowed",
            f"{raw.where}: {value} families wait for {_WAITING_INTERVALS[value]}",
        )
    raise BatchError(
        "interval_allowed", f"{raw.where}.interval must be one of {list(INTERVALS)}, got {value!r}"
    )


def _family(raw: _RawFamily) -> Family:
    where = raw.where
    return Family(
        family=raw.name,
        template=_matching(
            "front_matter",
            raw.fields["template"],
            f"{where}.template",
            _TEMPLATE,
            "a strategy name",
        ),
        interval=_interval(raw),
        grid=_grid(where, raw.fields["grid"]),
        card=_repo_path(raw.fields["card"], f"{where}.card"),
        card_sha256=_matching(
            "front_matter",
            raw.fields["card_sha256"],
            f"{where}.card_sha256",
            _SHA256,
            "64 lowercase hex digits",
        ),
        code_paths=_code_paths(where, raw.fields["code_paths"]),
        section=_text("front_matter", raw.fields["section"], f"{where}.section"),
        summary=_summary(where, raw.fields["summary"]),
    )


# ---------------------------------------------------------------------------
# Checks a file shows by itself
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Header:
    batch_id: str
    experiment: str
    status: Status
    families: tuple[_RawFamily, ...]


def is_sweep(text: str) -> bool:
    """True when ``text`` has readable front matter that says ``kind: sweep``."""
    try:
        front_matter, _ = _split(text)
    except BatchError:
        return False
    return front_matter.get("kind") == "sweep"


def _header(path: str, front_matter: Mapping[str, object]) -> _Header:
    _keys("front matter", front_matter, _TOP_KEYS, _OPTIONAL_TOP_KEYS)
    if front_matter["kind"] != "sweep":
        raise BatchError("front_matter", f"kind must be sweep, got {front_matter['kind']!r}")
    batch_id, experiment = (
        _matching("front_matter", front_matter[key], key, _ID, "a lowercase id")
        for key in ("batch_id", "experiment")
    )
    parts = PurePosixPath(path).parts[-_BATCH_PATH_PARTS:]
    expected = ("docs", "experiments", experiment, "batches", f"{batch_id}.md")
    if parts != expected:
        raise BatchError("front_matter", f"{path} is not {'/'.join(expected)}")
    status = front_matter["status"]
    if status not in _STATUSES:
        raise BatchError("front_matter", f"status must be DRAFT or SIGNED, got {status!r}")
    raws = _raw_families(front_matter)
    names = [r.name for r in raws]
    if repeated := sorted({n for n in names if names.count(n) > 1}):
        raise BatchError("front_matter", f"families named twice: {repeated}")
    return _Header(batch_id, experiment, "SIGNED" if status == "SIGNED" else "DRAFT", raws)


def _family_count(header: _Header) -> str:
    n = len(header.families)
    if n > MAX_FAMILIES:
        raise BatchError("family_count", f"{n} families; a sweep holds at most {MAX_FAMILIES}")
    return f"{n} families: {', '.join(r.name for r in header.families)}"


def _intervals_allowed(header: _Header) -> str:
    intervals = [_interval(r) for r in header.families]
    return f"intervals: {sorted(set(intervals))}"


def _summary_present(header: _Header) -> str:
    for raw in header.families:
        _summary(raw.where, raw.fields["summary"])
    return "every family has a summary"


def _section_present(header: _Header, body: str) -> str:
    headings = _headings(body)
    for raw in header.families:
        section = _text("section_present", raw.fields["section"], f"{raw.where}.section")
        found = [h for h in headings if h == section or h.startswith(f"{section} ")]
        if len(found) != 1:
            raise BatchError(
                "section_present",
                f"{raw.where}: {len(found)} headings start with {section!r}, not 1",
            )
    return "every family's pre-registration section is in the body"


def _parameters_cap(header: _Header) -> str:
    counts = [len(_grid(r.where, r.fields["grid"])) for r in header.families]
    return f"free parameters per family: {counts}"


def _max_candidates(front_matter: Mapping[str, object]) -> int:
    value = front_matter["max_candidates"]
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not MIN_CANDIDATES <= value <= MAX_SWEEP_CANDIDATES
    ):
        raise BatchError(
            "candidate_cap",
            f"max_candidates must be an integer from {MIN_CANDIDATES} to "
            f"{MAX_SWEEP_CANDIDATES}, got {value!r}",
        )
    return value


def _candidate_cap(header: _Header, front_matter: Mapping[str, object]) -> str:
    cap = _max_candidates(front_matter)
    n = len(expand(tuple(_family(r) for r in header.families)))
    if n > cap:
        raise BatchError("candidate_cap", f"the grids expand to {n} candidates, over {cap}")
    if n < MIN_CANDIDATES:
        raise BatchError(
            "candidate_cap", f"the grids expand to {n} candidate; at least {MIN_CANDIDATES}"
        )
    return f"{n} candidates, max_candidates {cap}"


def parse_sweep(path: str, text: str) -> SweepBatch:
    """Parse a sweep's text and refuse it at the first rule the file breaks by itself.

    Args:
        path: The sweep's repo-relative POSIX path.
        text: The file's content.

    Returns:
        The typed sweep, every candidate expanded. History checks are :func:`check_sweep`'s.

    Raises:
        BatchError: The first failed check, named.
    """
    front_matter, body = _split(text)
    header = _header(path, front_matter)
    _family_count(header)
    _summary_present(header)
    _section_present(header, body)
    _parameters_cap(header)
    families = tuple(_family(r) for r in header.families)
    _candidate_cap(header, front_matter)
    points = expand(families)
    by_name = {f.family: f for f in families}
    funnel, selection = _funnel(front_matter), _selection(front_matter)
    signed_by, date_signed, digest, scope, precursor, code_commit = _signature(
        front_matter, header.status
    )
    batch = ResearchBatch(
        path=path,
        experiment=header.experiment,
        batch_id=header.batch_id,
        candidates=tuple(_candidate(by_name[p.family], p) for p in points),
        body=body,
        funnel=funnel,
        selection=selection,
        status=header.status,
        code_commit=code_commit,
        signing_commit_precursor=precursor,
        content_sha256=digest,
        content_sha256_scope=scope,
        signed_by=signed_by,
        date_signed=date_signed,
    )
    return SweepBatch(batch, _max_candidates(front_matter), families, points)


# ---------------------------------------------------------------------------
# Checks that read history
# ---------------------------------------------------------------------------


def _per_family(sweep: SweepBatch) -> ResearchBatch:
    """The sweep with one stand-in candidate per family, named by the family.

    Cards and code belong to a family, so the history checks read each once
    and name the family in a failure, not one of its grid points.
    """
    first = {c.idea_family: c for c in reversed(sweep.batch.candidates)}
    return replace(
        sweep.batch,
        candidates=tuple(replace(first[f.family], strategy_id=f.family) for f in sweep.families),
    )


def _templates_known(
    sweep: SweepBatch,
    known: Collection[str],
    defaults: Mapping[str, Mapping[str, object]] | None,
) -> str:
    unknown = [f.template for f in sweep.families if f.template not in known]
    if unknown:
        raise BatchError("templates_known", f"not in config/strategies.yaml: {unknown}")
    if defaults is not None:
        for f in sweep.families:
            taken = defaults.get(f.template, {})
            for axis in f.grid:
                if axis.name not in taken:
                    raise BatchError(
                        "templates_known",
                        f"{f.family}: template {f.template} takes no parameter {axis.name}",
                    )
                default = taken[axis.name]
                if isinstance(default, int | Decimal) and type(default) is not type(axis.values[0]):
                    raise BatchError(
                        "templates_known",
                        f"{f.family}: {axis.name} is {type(default).__name__} in "
                        f"{f.template}, not {type(axis.values[0]).__name__}",
                    )
    return ", ".join(f"{f.family}={f.template}" for f in sweep.families)


def check_sweep(
    path: str,
    text: str,
    git: GitHistoryPort,
    *,
    known_strategies: Collection[str],
    require_signed: bool = False,
    strategy_parameters: Mapping[str, Mapping[str, object]] | None = None,
    head: str = "HEAD",
) -> tuple[BatchCheck, ...]:
    """Run every sweep check, in :data:`SWEEP_CHECK_NAMES` order.

    Args:
        path: Where the sweep sits in the repository, as a POSIX path.
        text: The document.
        git: The clone's history.
        known_strategies: The roster's names; a family's template must be one.
        require_signed: Also run ``status_signed``, which a DRAFT sweep fails.
        strategy_parameters: The roster's strategies with their default
            parameters. When given, ``templates_known`` also fails a grid axis
            the template does not take or whose values are of another kind.
        head: The commit that cards and code are read at.

    Returns:
        One check per name, ``status_signed`` only when required. Unreadable
        front matter fails ``front_matter`` and leaves the rest not checkable.
    """
    names = tuple(n for n in SWEEP_CHECK_NAMES if require_signed or n != "status_signed")
    try:
        front_matter, body = _split(text)
        header = _header(path, front_matter)
    except BatchError as exc:
        return tuple(
            BatchCheck(
                n, False, exc.message if n == "front_matter" else f"not checkable: {exc.message}"
            )
            for n in names
        )

    def after_parse(step: Callable[[SweepBatch], str]) -> Callable[[], str]:
        return lambda: step(parse_sweep(path, text))

    steps: dict[str, Callable[[], str]] = {
        "front_matter": lambda: f"sweep {header.batch_id} of {header.experiment}",
        "status_signed": lambda: _signed(header, front_matter),
        "content_sha256": after_parse(lambda s: _content_sha256(s.batch, text, git, head)),
        "family_count": lambda: _family_count(header),
        "interval_allowed": lambda: _intervals_allowed(header),
        "summary_present": lambda: _summary_present(header),
        "section_present": lambda: _section_present(header, body),
        "card_hashes": after_parse(lambda s: _card_hashes(_per_family(s), git, head)),
        "parameters_cap": lambda: _parameters_cap(header),
        "candidate_cap": lambda: _candidate_cap(header, front_matter),
        "funnel_floors": lambda: _funnel_described(front_matter),
        "selection": lambda: _selection_described(front_matter),
        "templates_known": after_parse(
            lambda s: _templates_known(s, known_strategies, strategy_parameters)
        ),
        "code_paths": after_parse(lambda s: _code_bound(_per_family(s), git, head)),
    }
    return tuple(_run(n, steps[n]) for n in names)


def _signed(header: _Header, front_matter: Mapping[str, object]) -> str:
    if header.status == "DRAFT":
        raise BatchError("status_signed", "status is DRAFT; the run needs a SIGNED sweep")
    _signature(front_matter, header.status)
    return f"signed by {front_matter['signed_by']}"
