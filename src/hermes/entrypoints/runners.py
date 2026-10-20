"""``hermes runners``: health of the self-hosted CI runners.

Reads ``GET /repos/{repo}/actions/runners`` and judges each label group
(``hermes``, ``hermes-ops``, ``hermes-review``, ``dependabot``) by the share of its runners that
are online. The runners are ephemeral Docker containers that re-register after
every job, so one runner offline between jobs is normal and never a failure.

Exit codes: 0 OK or DEGRADED, 1 FAIL (or the API refused the token), 2 usage,
3 ``HERMES_GITHUB_TOKEN`` not set.

Reference: change ``177-runner-health``.
"""

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, TextIO

import httpx

from hermes.entrypoints._health import EXIT_FAIL, EXIT_NOT_CONFIGURED, EXIT_OK

__all__ = [
    "DEFAULT_REPO",
    "GroupHealth",
    "Report",
    "Runner",
    "RunnerStatus",
    "classify",
    "fetch_runners",
    "group_status",
    "main",
]

DEFAULT_REPO: Final[str] = "ricardoafo/hermes"
_API_URL: Final[str] = "https://api.github.com/repos/{repo}/actions/runners"
# The planned fleet is about ten runners; one page holds them all.
_PER_PAGE: Final[int] = 100
_TIMEOUT_SECONDS: Final[float] = 10.0

# The CI pool. A repo with no runner labelled this has no CI at all.
REQUIRED_LABELS: Final[tuple[str, ...]] = ("hermes",)
# Planned pools: judged once they exist, ignored until then.
OPTIONAL_LABELS: Final[tuple[str, ...]] = ("hermes-ops", "hermes-review", "dependabot")
# Ephemeral runners are offline between jobs, so a group only counts as
# degraded when fewer than this share (1/2) of its runners is online.
_MIN_ONLINE_SHARE: Final[tuple[int, int]] = (1, 2)

_USAGE = "Usage: hermes runners [--json]\n"


class RunnerStatus:
    """Status labels; :data:`_SEVERITY` orders them from best to worst."""

    OK: Final[str] = "OK"
    DEGRADED: Final[str] = "DEGRADED"
    FAIL: Final[str] = "FAIL"


_SEVERITY: Final[dict[str, int]] = {
    RunnerStatus.OK: 0,
    RunnerStatus.DEGRADED: 1,
    RunnerStatus.FAIL: 2,
}


@dataclass(frozen=True, slots=True)
class Runner:
    """One runner as the GitHub API reports it.

    Attributes:
        name: Runner name.
        online: True when GitHub reports it ``online``.
        busy: True while it runs a job; a busy runner is online.
        labels: Its label names.
    """

    name: str
    online: bool
    busy: bool
    labels: frozenset[str]


@dataclass(frozen=True, slots=True)
class GroupHealth:
    """Health of the runners sharing one label."""

    label: str
    online: int
    total: int
    busy: int
    status: str


@dataclass(frozen=True, slots=True)
class Report:
    """Per-label health plus the worst status across them."""

    groups: tuple[GroupHealth, ...]
    status: str

    @property
    def exit_code(self) -> int:
        """FAIL exits 1; OK and DEGRADED exit 0."""
        return EXIT_FAIL if self.status == RunnerStatus.FAIL else EXIT_OK


def group_status(online: int, total: int) -> str:
    """Judge one label group from its online and total runner counts.

    Args:
        online: Runners online (busy ones included).
        total: Runners registered under the label.

    Returns:
        FAIL when none is online, DEGRADED when fewer than half are online,
        OK otherwise.
    """
    if online == 0:
        return RunnerStatus.FAIL
    numerator, denominator = _MIN_ONLINE_SHARE
    if online * denominator < total * numerator:
        return RunnerStatus.DEGRADED
    return RunnerStatus.OK


def classify(runners: Sequence[Runner]) -> Report:
    """Group the runners by label and judge each group.

    A required label with no runner is FAIL; an optional one with no runner is
    left out. A runner with several labels counts in each group.

    Args:
        runners: Every runner of the repository.

    Returns:
        The per-label rows and the worst status among them.
    """
    groups: list[GroupHealth] = []
    for label in (*REQUIRED_LABELS, *OPTIONAL_LABELS):
        members = [r for r in runners if label in r.labels]
        if not members and label not in REQUIRED_LABELS:
            continue
        online = sum(1 for r in members if r.online)
        groups.append(
            GroupHealth(
                label=label,
                online=online,
                total=len(members),
                busy=sum(1 for r in members if r.busy),
                status=group_status(online, len(members)),
            )
        )
    overall = max((g.status for g in groups), key=_SEVERITY.__getitem__)
    return Report(groups=tuple(groups), status=overall)


def fetch_runners(repo: str, token: str, *, client: httpx.Client | None = None) -> list[Runner]:
    """Read the repository's runners from the GitHub API.

    Args:
        repo: ``owner/name``.
        token: Token with repository Administration read access.
        client: HTTP client; a short-lived one is made when omitted.

    Returns:
        The runners.

    Raises:
        httpx.HTTPStatusError: The API answered with an error status.
        httpx.HTTPError: The request failed in transit.
    """
    owned = client is None
    http = client or httpx.Client(timeout=_TIMEOUT_SECONDS)
    try:
        response = http.get(
            _API_URL.format(repo=repo),
            params={"per_page": _PER_PAGE},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
    finally:
        if owned:
            http.close()
    return [
        Runner(
            name=str(item["name"]),
            online=item["status"] == "online",
            busy=bool(item["busy"]),
            labels=frozenset(str(label["name"]) for label in item["labels"]),
        )
        for item in payload["runners"]
    ]


def _render_table(repo: str, report: Report, stream: TextIO) -> None:
    stream.write(f"# hermes runners ({repo})\n")
    # 12 fits every label but hermes-review, which widens the column.
    width = max([12, *(len(g.label) + 1 for g in report.groups)])
    stream.write(f"{'label':<{width}} {'online':<8} {'busy':<5} status\n")
    stream.writelines(
        f"{g.label:<{width}} {f'{g.online}/{g.total}':<8} {g.busy:<5} {g.status}\n"
        for g in report.groups
    )
    stream.write(f"overall: {report.status}\n")


def _render_json(repo: str, report: Report, stream: TextIO) -> None:
    body = {
        "repo": repo,
        "status": report.status,
        "groups": [
            {
                "label": g.label,
                "online": g.online,
                "total": g.total,
                "busy": g.busy,
                "status": g.status,
            }
            for g in report.groups
        ],
    }
    stream.write(json.dumps(body) + "\n")


def main(
    argv: list[str],
    *,
    stream: TextIO,
    env: Mapping[str, str],
    client: httpx.Client | None = None,
) -> int:
    """Run ``hermes runners``.

    Args:
        argv: Residual argv after the ``runners`` token.
        stream: Output stream.
        env: Env-var snapshot (``HERMES_GITHUB_TOKEN``, ``HERMES_GITHUB_REPO``).
        client: HTTP client, for tests.

    Returns:
        The process exit code.
    """
    parser = argparse.ArgumentParser(prog="hermes runners", add_help=False)
    parser.add_argument("--json", action="store_true", dest="as_json")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        stream.write(_USAGE)
        return 2

    token = env.get("HERMES_GITHUB_TOKEN", "").strip()
    if not token:
        stream.write("NOT CONFIGURED: set HERMES_GITHUB_TOKEN in .env.local\n")
        return EXIT_NOT_CONFIGURED
    repo = env.get("HERMES_GITHUB_REPO", "").strip() or DEFAULT_REPO

    try:
        runners = fetch_runners(repo, token, client=client)
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code in (401, 403):
            stream.write(
                f"FAIL: GitHub answered {code}; the runners API needs a token "
                "with repository Administration read access\n"
            )
        else:
            stream.write(f"FAIL: GitHub answered {code}\n")
        return EXIT_FAIL
    except httpx.HTTPError as exc:
        stream.write(f"FAIL: could not reach GitHub: {exc}\n")
        return EXIT_FAIL

    report = classify(runners)
    if args.as_json:
        _render_json(repo, report, stream)
    else:
        _render_table(repo, report, stream)
    return report.exit_code
