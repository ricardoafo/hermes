"""``hermes tax-pack fetch`` (change 727-tax-pack-fetch R-1 to R-3).

The ssh runner is the only fake: it returns a tar archive built the way the
server's ``hermes tax-pack`` writes a folder (``render_manifest`` is the
production manifest writer), so the verification runs on real manifests.
Exit codes: 0 fetched, 1 ssh or server failure, 2 usage, 4 manifest mismatch.
"""

import hashlib
import io
import json
import shutil
import sys
import tarfile
from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from hermes.application.filing.pack import MANIFEST_FILE, render_manifest
from hermes.entrypoints import tax_pack_fetch
from hermes.entrypoints.commands import discover
from hermes.entrypoints.tax_pack_fetch import (
    EXIT_UNVERIFIED,
    REMOTE_PACK_DIR,
    FetchResult,
    build_fetch_argv,
    fetch_main,
    verify_pack,
)
from hermes.entrypoints.vps import VpsSettings

_YEAR = 2026
_AT = datetime(2026, 10, 4, 5, 30, tzinfo=UTC)
_BODY = {
    "README.md": b"# Tax pack 2026\n",
    "renta-web.pdf": b"%PDF-1.4 fake renta sheet",
    "trail.csv": b"sale,lot\n1,2\n",
}


def _pack(year: int = _YEAR, overrides: dict[str, bytes] | None = None) -> dict[str, bytes]:
    """Return the files of a server pack, with the manifest the production writer renders."""
    body = {**_BODY, **(overrides or {})}
    return {**body, MANIFEST_FILE: render_manifest(year, _AT, body).encode()}


def _archive(
    files: dict[str, bytes], year: int = _YEAR, extra: list[tarfile.TarInfo] | None = None
) -> bytes:
    """Return a tar stream of ``<year>/<name>``, as the server's ``tar -cf -`` writes it."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        directory = tarfile.TarInfo(str(year))
        directory.type = tarfile.DIRTYPE
        tar.addfile(directory)
        for name, content in files.items():
            info = tarfile.TarInfo(f"{year}/{name}")
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        for info in extra or []:
            tar.addfile(info)
    return buffer.getvalue()


class _Runner:
    """Returns one scripted ssh result and records the argv it was asked to run."""

    def __init__(self, result: FetchResult) -> None:
        self.result = result
        self.argvs: list[list[str]] = []

    def __call__(self, argv: list[str]) -> FetchResult:
        self.argvs.append(argv)
        return self.result


def _ok(files: dict[str, bytes] | None = None) -> _Runner:
    return _Runner(FetchResult(0, _archive(files if files is not None else _pack()), ""))


def _run(
    tmp_path: Path, runner: _Runner, *args: str, env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    code = fetch_main(
        [*args],
        stream=out,
        default_out=tmp_path / "default",
        env=env if env is not None else {},
        runner=runner,
        err=err,
    )
    return code, out.getvalue(), err.getvalue()


def test_a_verified_pack_is_written_under_server_year(tmp_path: Path) -> None:
    """R-1: a pack that matches its manifest lands byte for byte in <out>/server/<year>/."""
    code, out, _ = _run(tmp_path, _ok(), "--year", "2026", "--out", str(tmp_path / "packs"))
    folder = tmp_path / "packs" / "server" / "2026"
    assert code == 0
    assert out == f"Fetched and verified 4 files into {folder}\n"
    assert {p.name for p in folder.iterdir()} == {*_BODY, MANIFEST_FILE}
    assert (folder / "trail.csv").read_bytes() == b"sale,lot\n1,2\n"


def test_the_default_folder_is_used_without_out(tmp_path: Path) -> None:
    """R-1: with no --out the pack goes under the folder `hermes tax-pack` itself uses."""
    code, _, _ = _run(tmp_path, _ok(), "--year", "2026")
    assert code == 0
    assert (tmp_path / "default" / "server" / "2026" / MANIFEST_FILE).is_file()


def test_a_changed_file_is_refused_and_nothing_is_written(tmp_path: Path) -> None:
    """R-2: one flipped byte fails loudly (exit 4) and leaves no folder behind."""
    tampered = _pack()
    tampered["trail.csv"] = b"sale,lot\n1,3\n"
    code, _, err = _run(tmp_path, _ok(tampered), "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert (
        err
        == "ERROR: the 2026 pack was not written: trail.csv does not match its manifest checksum\n"
    )
    assert not (tmp_path / "p").exists()


def test_a_missing_file_is_refused(tmp_path: Path) -> None:
    """R-2: a file the manifest lists but the archive lacks is a mismatch."""
    files = _pack()
    del files["README.md"]
    code, _, err = _run(tmp_path, _ok(files), "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert "missing: ['README.md']" in err


def test_an_unlisted_file_is_refused(tmp_path: Path) -> None:
    """R-2: a file the manifest does not list is never written."""
    files = _pack()
    files["extra.txt"] = b"not in the manifest"
    code, _, err = _run(tmp_path, _ok(files), "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert "not listed: ['extra.txt']" in err


def test_a_pack_without_a_manifest_is_refused(tmp_path: Path) -> None:
    """R-2: no manifest, no way to check, nothing written."""
    files = _pack()
    del files[MANIFEST_FILE]
    code, _, err = _run(tmp_path, _ok(files), "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert err == "ERROR: the 2026 pack was not written: the pack has no manifest.json\n"


def test_a_manifest_for_another_year_is_refused(tmp_path: Path) -> None:
    """R-2: the 2025 pack in the 2026 folder name is not a 2026 pack."""
    code, _, err = _run(
        tmp_path, _ok(_pack(year=2025)), "--year", "2026", "--out", str(tmp_path / "p")
    )
    assert code == EXIT_UNVERIFIED
    assert (
        err
        == "ERROR: the 2026 pack was not written: manifest.json is for tax year 2025, not 2026\n"
    )


@pytest.mark.parametrize("name", ["../escape.txt", "2026/sub/deep.txt", "other/file.txt"])
def test_an_entry_outside_the_year_folder_is_refused(tmp_path: Path, name: str) -> None:
    """R-2: a path that climbs out of <year>/ or nests inside it never reaches the disk."""
    info = tarfile.TarInfo(name)
    info.size = 0
    runner = _Runner(FetchResult(0, _archive(_pack(), extra=[info]), ""))
    code, _, err = _run(tmp_path, runner, "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert (
        err == f"ERROR: the 2026 pack was not written: unexpected entry {name!r} in the archive\n"
    )
    assert not (tmp_path / "p").exists()


def test_a_link_in_the_archive_is_refused(tmp_path: Path) -> None:
    """R-2: only regular files are accepted, so a symlink cannot point at a local file."""
    link = tarfile.TarInfo("2026/passwd")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    runner = _Runner(FetchResult(0, _archive(_pack(), extra=[link]), ""))
    code, _, err = _run(tmp_path, runner, "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert "unexpected entry '2026/passwd'" in err


def test_a_stream_that_is_not_a_tar_is_refused(tmp_path: Path) -> None:
    """R-2: a banner or an error page on stdout is not a pack."""
    runner = _Runner(FetchResult(0, b"Welcome to hermes-01\n" * 100, ""))
    code, _, err = _run(tmp_path, runner, "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert (
        err == "ERROR: the 2026 pack was not written: the server did not send a readable archive\n"
    )


def test_a_failed_fetch_keeps_the_previous_copy(tmp_path: Path) -> None:
    """R-2: a bad refetch never destroys the pack fetched earlier."""
    out = tmp_path / "p"
    assert _run(tmp_path, _ok(), "--year", "2026", "--out", str(out))[0] == 0
    tampered = _pack()
    tampered["trail.csv"] = b"changed"
    assert _run(tmp_path, _ok(tampered), "--year", "2026", "--out", str(out))[0] == EXIT_UNVERIFIED
    assert (out / "server" / "2026" / "trail.csv").read_bytes() == b"sale,lot\n1,2\n"


def test_a_refetch_replaces_the_previous_copy(tmp_path: Path) -> None:
    """R-1: the second fetch leaves exactly the second pack, with no leftover file."""
    out = tmp_path / "p"
    _run(tmp_path, _ok(), "--year", "2026", "--out", str(out))
    newer = {"README.md": b"# newer\n"}
    code, _, _ = _run(tmp_path, _ok(_pack_only(newer)), "--year", "2026", "--out", str(out))
    folder = out / "server" / "2026"
    assert code == 0
    assert {p.name for p in folder.iterdir()} == {"README.md", MANIFEST_FILE}


def _pack_only(body: dict[str, bytes]) -> dict[str, bytes]:
    return {**body, MANIFEST_FILE: render_manifest(_YEAR, _AT, body).encode()}


def test_ssh_reads_the_year_folder_with_tar_and_no_terminal(tmp_path: Path) -> None:
    """R-1: the remote command is a fixed tar of one year; ssh never prompts."""
    runner = _ok()
    _run(
        tmp_path,
        runner,
        "--year",
        "2026",
        env={"HERMES_VPS_HOST": "box", "HERMES_VPS_USER": "ops", "HERMES_VPS_KEY": "/k/key"},
    )
    assert runner.argvs == [
        [
            "ssh", "-i", "/k/key", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "ops@box", f"tar -C {REMOTE_PACK_DIR} -cf - 2026",
        ]
    ]  # fmt: skip


def test_ssh_failure_reports_the_last_diagnostic(tmp_path: Path) -> None:
    """R-3: an unreachable server is exit 1 with the reason, and nothing is written."""
    runner = _Runner(FetchResult(255, b"", "ssh: connect to host box: Connection timed out\n"))
    code, _, err = _run(
        tmp_path, runner, "--year", "2026", "--out", str(tmp_path / "p"),
        env={"HERMES_VPS_HOST": "box"},
    )  # fmt: skip
    assert code == 1
    assert err == (
        "ERROR: could not fetch the 2026 pack from box: "
        "ssh: connect to host box: Connection timed out\n"
    )
    assert not (tmp_path / "p").exists()


def test_a_year_with_no_pack_on_the_server_is_exit_1(tmp_path: Path) -> None:
    """R-3: tar's "Cannot stat" for a year that has no folder is reported, not a crash."""
    runner = _Runner(FetchResult(2, b"", "tar: 2031: Cannot stat: No such file or directory\n"))
    code, _, err = _run(tmp_path, runner, "--year", "2031", env={"HERMES_VPS_HOST": "box"})
    assert code == 1
    assert err.endswith("tar: 2031: Cannot stat: No such file or directory\n")


def test_a_missing_ssh_client_is_a_usage_error(tmp_path: Path) -> None:
    """R-3: no OpenSSH client says so (exit 2) instead of a traceback."""

    def runner(_argv: list[str]) -> FetchResult:
        raise FileNotFoundError

    out, err = io.StringIO(), io.StringIO()
    code = fetch_main(
        ["--year", "2026"], stream=out, default_out=tmp_path, env={}, runner=runner, err=err
    )
    assert code == 2
    assert err.getvalue() == "ERROR: ssh not found; install the OpenSSH client.\n"


@pytest.mark.parametrize("argv", [[], ["--year"], ["--year", "abc"], ["--year", "26"], ["--bogus"]])
def test_a_bad_command_line_is_exit_2(tmp_path: Path, argv: list[str]) -> None:
    """R-3: a missing or malformed --year never reaches ssh."""
    runner = _ok()
    code, _, err = _run(tmp_path, runner, *argv)
    assert code == 2
    assert err.startswith("ERROR: ")
    assert runner.argvs == []


def test_a_host_that_ssh_could_read_as_an_option_is_refused(tmp_path: Path) -> None:
    """R-3: HERMES_VPS_HOST="-oProxyCommand=..." cannot become an ssh option."""
    runner = _ok()
    code, _, err = _run(
        tmp_path, runner, "--year", "2026", env={"HERMES_VPS_HOST": "-oProxyCommand=x"}
    )
    assert code == 2
    assert err == "ERROR: HERMES_VPS_HOST and HERMES_VPS_USER must be plain names.\n"
    assert runner.argvs == []


def test_help_prints_usage_and_exits_0(tmp_path: Path) -> None:
    """R-3: --help is exit 0 on stdout and never connects."""
    runner = _ok()
    code, out, _ = _run(tmp_path, runner, "--help")
    assert code == 0
    assert out.startswith("Usage: hermes tax-pack fetch --year YYYY [--out DIR]\n")
    assert runner.argvs == []


def test_the_command_routes_fetch_to_the_fetcher() -> None:
    """R-3: `hermes tax-pack fetch --help` reaches the fetcher, not the refresh."""
    command = discover()["tax-pack"]
    out = io.StringIO()
    assert command.run(["fetch", "--help"], stream=out) == 0
    assert out.getvalue().startswith("Usage: hermes tax-pack fetch")


def test_the_remote_command_names_one_numeric_year() -> None:
    """R-1: the year in the remote command is an int, so no shell text can ride along."""
    argv = build_fetch_argv(VpsSettings("h", "u", Path("/k")), 2026)
    assert argv[-1] == f"tar -C {REMOTE_PACK_DIR} -cf - 2026"


@given(
    st.dictionaries(
        st.from_regex(r"[a-z][a-z0-9.-]{0,15}", fullmatch=True).filter(
            lambda n: n != MANIFEST_FILE
        ),
        st.binary(max_size=64),
        min_size=1,
        max_size=6,
    ),
    st.data(),
)
def test_any_changed_byte_in_any_file_fails_verification(
    body: dict[str, bytes], data: st.DataObject
) -> None:
    """R-2: verify_pack accepts exactly the manifest's content and rejects any one-byte change."""
    files = {**body, MANIFEST_FILE: render_manifest(_YEAR, _AT, body).encode()}
    assert verify_pack(_YEAR, files) is None
    victim = data.draw(st.sampled_from(sorted(body)))
    original = body[victim]
    changed = original[:-1] + bytes([original[-1] ^ 1]) if original else b"x"
    assert hashlib.sha256(changed).digest() != hashlib.sha256(original).digest()
    assert verify_pack(_YEAR, {**files, victim: changed}) == (
        f"{victim} does not match its manifest checksum"
    )


def test_a_manifest_that_is_not_json_is_refused() -> None:
    """R-2: a corrupt manifest reads as "not a pack manifest", never a traceback."""
    assert (
        verify_pack(_YEAR, {MANIFEST_FILE: b"{not json"}) == "manifest.json is not a pack manifest"
    )
    assert verify_pack(_YEAR, {MANIFEST_FILE: json.dumps({"x": 1}).encode()}) == (
        "manifest.json is not a pack manifest"
    )


def test_the_real_runner_reads_stdout_stderr_and_the_exit_code() -> None:
    """R-1: the subprocess seam returns the child's bytes, diagnostics and exit code unchanged."""
    program = "import sys; sys.stdout.buffer.write(b'abc'); sys.stderr.write('warn'); sys.exit(3)"
    result = tax_pack_fetch._subprocess_runner([sys.executable, "-c", program])
    assert result == FetchResult(3, b"abc", "warn")


def test_the_real_runner_stops_reading_past_the_size_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """R-2: a server that streams more than a pack can hold is cut off, not buffered."""
    monkeypatch.setattr(tax_pack_fetch, "_MAX_PACK_BYTES", 10)
    program = "import sys; sys.stdout.buffer.write(b'x' * 1000)"
    result = tax_pack_fetch._subprocess_runner([sys.executable, "-c", program])
    assert result.archive == b"x" * 11


def test_an_oversized_stream_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """R-2: more bytes than the cap is exit 4 and nothing is written."""
    monkeypatch.setattr(tax_pack_fetch, "_MAX_PACK_BYTES", 10)
    code, _, err = _run(tmp_path, _ok(), "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert (
        err
        == "ERROR: the 2026 pack was not written: the archive is larger than a tax pack can be\n"
    )
    assert not (tmp_path / "p").exists()


@pytest.mark.skipif(shutil.which("tar") is None, reason="needs a tar command")
def test_a_pack_streamed_by_the_real_tar_command_verifies(tmp_path: Path) -> None:
    """R-1: the archive the server's `tar -C <dir> -cf - <year>` writes parses and verifies.

    Seam: the only part not run is ssh itself; the tar command and the subprocess
    runner are the real ones, pointed at a local folder.
    """
    folder = tmp_path / "server" / "2026"
    folder.mkdir(parents=True)
    for name, content in _pack().items():
        (folder / name).write_bytes(content)

    def local_tar(_ssh_argv: list[str]) -> FetchResult:
        return tax_pack_fetch._subprocess_runner(
            ["tar", "-C", str(tmp_path / "server"), "-cf", "-", "2026"]
        )

    out, err = io.StringIO(), io.StringIO()
    code = fetch_main(
        ["--year", "2026"],
        stream=out,
        default_out=tmp_path / "pc",
        env={},
        runner=local_tar,
        err=err,
    )
    assert code == 0
    assert (tmp_path / "pc" / "server" / "2026" / "trail.csv").read_bytes() == b"sale,lot\n1,2\n"


def test_a_crash_while_swapping_never_loses_the_earlier_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-2: if the new folder cannot be moved in, the earlier fetched pack is put back."""
    out = tmp_path / "p"
    assert _run(tmp_path, _ok(), "--year", "2026", "--out", str(out))[0] == 0
    destination = out / "server" / "2026"
    real_replace = Path.replace

    def failing_replace(self: Path, target: Path) -> Path:
        if self.name == ".2026.fetching":
            raise OSError("disk full")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", failing_replace)
    with pytest.raises(OSError, match="disk full"):
        _run(tmp_path, _ok(), "--year", "2026", "--out", str(out))
    monkeypatch.undo()
    assert (destination / "trail.csv").read_bytes() == b"sale,lot\n1,2\n"


def test_the_real_runner_survives_a_child_that_writes_a_lot_to_stderr() -> None:
    """R-3: stderr far larger than a pipe buffer neither stalls the run nor loses the last line."""
    program = (
        "import sys; sys.stderr.write('x' * 500000 + '\\nlast line\\n'); "
        "sys.stdout.buffer.write(b'ok')"
    )
    result = tax_pack_fetch._subprocess_runner([sys.executable, "-c", program])
    assert result.archive == b"ok"
    assert result.stderr.splitlines()[-1] == "last line"


def test_a_stream_cut_off_at_the_cap_reports_the_size_not_an_ssh_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R-2: the runner kills ssh past the cap; that exit must not hide the real reason."""
    monkeypatch.setattr(tax_pack_fetch, "_MAX_PACK_BYTES", 10)
    runner = _Runner(FetchResult(-9, b"x" * 11, ""))
    code, _, err = _run(tmp_path, runner, "--year", "2026", "--out", str(tmp_path / "p"))
    assert code == EXIT_UNVERIFIED
    assert err == (
        "ERROR: the 2026 pack was not written: the archive is larger than a tax pack can be\n"
    )
