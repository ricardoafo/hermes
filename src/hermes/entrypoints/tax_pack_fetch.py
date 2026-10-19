"""``hermes tax-pack fetch``: bring the server's tax pack to this PC.

Runs ``tar`` on the VPS over ssh, reads the archive from ssh's stdout, checks
every file against the pack's ``manifest.json`` and only then writes
``<out>/server/<year>/``. A pack that does not verify is never written. The
server holds the filing figures only; nothing leaves this PC and nothing is
submitted. The folder is separate from ``<out>/<year>/`` so a local run with
the taxpayer profile is never overwritten.

Exit codes: 0 fetched and verified, 1 ssh or the server failed, 2 usage,
4 the pack does not match its manifest.

Reference: openspec/changes/727-tax-pack-fetch/change.md.
"""

import argparse
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final, TextIO

from hermes.application.filing.pack import MANIFEST_FILE
from hermes.entrypoints._streams import resolve_err
from hermes.entrypoints.vps import VpsSettings

__all__ = [
    "EXIT_UNVERIFIED",
    "REMOTE_PACK_DIR",
    "SERVER_SUBDIR",
    "FetchResult",
    "build_fetch_argv",
    "fetch_main",
    "verify_pack",
]

EXIT_UNVERIFIED: Final[int] = 4
REMOTE_PACK_DIR: Final[str] = "/var/lib/hermes-tax-pack"
SERVER_SUBDIR: Final[str] = "server"
_MAX_PACK_BYTES: Final[int] = 64 * 1024 * 1024
_CONNECT_TIMEOUT_SECONDS: Final[int] = 10
_SAFE_TOKEN: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

_USAGE: Final[str] = (
    "Usage: hermes tax-pack fetch --year YYYY [--out DIR]\n"
    "\n"
    "Copies the server's tax pack for YEAR to DIR/server/YEAR/ over ssh and\n"
    "checks every file against the pack's manifest. Hermes submits nothing and\n"
    "uploads nothing. Settings: HERMES_VPS_HOST, HERMES_VPS_USER, HERMES_VPS_KEY.\n"
    "\n"
    "Exit codes: 0 fetched, 1 ssh or server failure, 2 usage, 4 manifest mismatch.\n"
)


@dataclass(frozen=True, slots=True)
class FetchResult:
    """What the ssh process left behind.

    Attributes:
        returncode: ssh's exit code, which is ``tar``'s once ssh connected.
        archive: Bytes ``tar`` wrote to stdout.
        stderr: ssh and ``tar`` diagnostics.
    """

    returncode: int
    archive: bytes
    stderr: str


FetchRunner = Callable[[list[str]], FetchResult]


def build_fetch_argv(settings: VpsSettings, year: int) -> list[str]:
    """Return the ssh argv that streams the year's pack folder as a tar archive.

    Args:
        settings: Host, user and key.
        year: Tax year; it names the remote folder.

    Returns:
        The argument list for :mod:`subprocess` (no local shell involved).
    """
    return [
        "ssh",
        "-i",
        str(settings.key),
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={_CONNECT_TIMEOUT_SECONDS}",
        f"{settings.user}@{settings.host}",
        f"tar -C {REMOTE_PACK_DIR} -cf - {year}",
    ]


def _subprocess_runner(argv: list[str]) -> FetchResult:
    """Run ssh, keeping at most one byte past the size cap of its stdout.

    Reading stops (and ssh is killed) as soon as the stream is larger than a tax
    pack can be, so memory stays bounded whatever the server sends.
    """
    with subprocess.Popen(  # noqa: S603 - argv list, fixed ssh binary
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    ) as proc:
        assert proc.stdout is not None  # noqa: S101 - PIPE guarantees it
        archive = proc.stdout.read(_MAX_PACK_BYTES + 1)
        if len(archive) > _MAX_PACK_BYTES:
            proc.kill()
        _, stderr = proc.communicate()
        return FetchResult(proc.returncode, archive, stderr.decode("utf-8", errors="replace"))


def verify_pack(year: int, files: Mapping[str, bytes]) -> str | None:
    """Check a fetched pack against its manifest.

    Args:
        year: The tax year that was asked for.
        files: File name to bytes, as received.

    Returns:
        ``None`` when the pack verifies, else one line saying what is wrong.
    """
    raw = files.get(MANIFEST_FILE)
    if raw is None:
        return f"the pack has no {MANIFEST_FILE}"
    try:
        manifest = json.loads(raw)
        listed = manifest["sha256"]
        manifest_year = manifest["tax_year"]
    except ValueError, KeyError, TypeError:
        return f"{MANIFEST_FILE} is not a pack manifest"
    if manifest_year != year or not isinstance(listed, dict):
        return f"{MANIFEST_FILE} is for tax year {manifest_year}, not {year}"
    received = {name for name in files if name != MANIFEST_FILE}
    if received != set(listed):
        extra = sorted(received - set(listed))
        missing = sorted(set(listed) - received)
        return f"file list differs from the manifest (not listed: {extra}; missing: {missing})"
    for name in sorted(received):
        if hashlib.sha256(files[name]).hexdigest() != listed[name]:
            return f"{name} does not match its manifest checksum"
    return None


def _read_archive(archive: bytes, year: int) -> dict[str, bytes] | str:
    """Return the flat file map of ``<year>/`` in ``archive``, or one line on a bad archive."""
    if len(archive) > _MAX_PACK_BYTES:
        return "the archive is larger than a tax pack can be"
    files: dict[str, bytes] = {}
    total = 0
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as tar:
            for member in tar:
                path = PurePosixPath(member.name)
                if member.isdir():
                    continue
                if not member.isfile() or path.parent != PurePosixPath(str(year)):
                    return f"unexpected entry {member.name!r} in the archive"
                total += member.size
                if total > _MAX_PACK_BYTES:
                    return "the archive is larger than a tax pack can be"
                handle = tar.extractfile(member)
                if handle is None:
                    return f"cannot read {member.name!r} from the archive"
                files[path.name] = handle.read()
    except tarfile.TarError:
        return "the server did not send a readable archive"
    return files


def _parse(argv: list[str], stream: TextIO, err: TextIO) -> argparse.Namespace | int:
    """Parse arguments; an exit code when there is nothing to run."""
    parser = argparse.ArgumentParser(prog="hermes tax-pack fetch", add_help=False)
    parser.add_argument("--year", type=int)
    parser.add_argument("--out", type=Path)
    parser.add_argument("-h", "--help", action="store_true")
    try:
        parsed = parser.parse_args(argv)
    except SystemExit:
        err.write(f"ERROR: could not parse `hermes tax-pack fetch` arguments.\n{_USAGE}")
        return 2
    if parsed.help:
        stream.write(_USAGE)
        return 0
    if parsed.year is None or not 2000 <= parsed.year <= 2999:  # noqa: PLR2004 - four-digit year
        err.write(f"ERROR: --year YYYY is required.\n{_USAGE}")
        return 2
    return parsed


def _install(destination: Path, files: Mapping[str, bytes]) -> None:
    """Write ``files`` next to ``destination``, then swap the folder in, the old one aside first."""
    staging = destination.with_name(f".{destination.name}.fetching")
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    for name, content in files.items():
        (staging / name).write_bytes(content)
    previous = destination.with_name(f".{destination.name}.previous")
    shutil.rmtree(previous, ignore_errors=True)
    if destination.exists():
        destination.replace(previous)
    try:
        staging.replace(destination)
    except OSError:
        if previous.exists():
            previous.replace(destination)
        raise
    shutil.rmtree(previous, ignore_errors=True)


def fetch_main(
    argv: list[str],
    *,
    stream: TextIO,
    default_out: Path,
    env: Mapping[str, str] | None = None,
    runner: FetchRunner = _subprocess_runner,
    err: TextIO | None = None,
) -> int:
    """Fetch and verify the server's tax pack for one year.

    Args:
        argv: Arguments after ``tax-pack fetch``.
        stream: Output stream.
        default_out: Pack folder used when ``--out`` is not given.
        env: Environment snapshot (defaults to ``os.environ``).
        runner: Runs the ssh argv; tests inject a fake so nothing connects.
        err: Stream for ERROR lines; defaults to ``sys.stderr``.

    Returns:
        Process exit code.
    """
    err = resolve_err(err)
    parsed = _parse(argv, stream, err)
    if isinstance(parsed, int):
        return parsed
    snapshot = dict(os.environ if env is None else env)
    settings = VpsSettings.from_env(snapshot)
    if not (_SAFE_TOKEN.fullmatch(settings.host) and _SAFE_TOKEN.fullmatch(settings.user)):
        err.write("ERROR: HERMES_VPS_HOST and HERMES_VPS_USER must be plain names.\n")
        return 2
    try:
        result = runner(build_fetch_argv(settings, parsed.year))
    except FileNotFoundError:
        err.write("ERROR: ssh not found; install the OpenSSH client.\n")
        return 2
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()
        err.write(
            f"ERROR: could not fetch the {parsed.year} pack from {settings.host}"
            f"{': ' + detail[-1] if detail else ''}\n"
        )
        return 1
    files = _read_archive(result.archive, parsed.year)
    problem = files if isinstance(files, str) else verify_pack(parsed.year, files)
    if problem is not None or isinstance(files, str):
        err.write(f"ERROR: the {parsed.year} pack was not written: {problem}\n")
        return EXIT_UNVERIFIED
    destination = (parsed.out or default_out) / SERVER_SUBDIR / str(parsed.year)
    destination.parent.mkdir(parents=True, exist_ok=True)
    _install(destination, files)
    stream.write(f"Fetched and verified {len(files)} files into {destination}\n")
    return 0
