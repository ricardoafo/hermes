"""Tier 2 invariants for the one-time guards in ``scripts/deploy/vps_bootstrap.sh``.

The script runs as root against absolute paths and arrives on the VPS over
stdin, so these tests extract each guard function from the script text and run
it under bash with stub ``uv``, ``curl``, ``runuser``, ``visudo``, ``id``,
``chown`` and ``install`` binaries. Every guard must check before it acts, so
each one is run twice and the second run must change nothing.

Covered: ``require_tools``, ``ensure_uv``, ``ensure_python``, ``install_units``,
``render_deploy_sudoers`` and ``ensure_deploy_sudoers``. The operator
acceptance run (openspec/changes/179-cd-first-real-deploy/change.md, R-5) covers
the real box.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

from tests.integration.deploy._bash import (
    NEEDS_POSIX_MODES,
    RUN_ON_THIS_PLATFORM,
    SCRIPT_DIR,
    SKIP_REASON,
    clean_env,
    output,
    posix,
    run_bash,
    write_stubs,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN_ON_THIS_PLATFORM, reason=SKIP_REASON),
]

_SCRIPT = SCRIPT_DIR / "vps_bootstrap.sh"
_PYTHON = "3.14.7"

_STUB_LOG = '''echo "$(basename "$0") $*" >> "$STUB_LOG"'''

_STUB_UV = f"""#!/usr/bin/env bash
{_STUB_LOG}
case "$1 $2" in
    "--version "*) echo "uv $(cat "$STUB_DIR/uv.version")" ;;
    "python find")
        if [ -n "${{STUB_FIND_OUTSIDE:-}}" ]; then echo "/usr/bin/python3.14"; exit 0; fi
        [ -f "$STUB_DIR/python.installed" ] || exit 1
        echo "$PYTHON_DIR/cpython-{_PYTHON}-linux/bin/python3.14" ;;
    "python install") touch "$STUB_DIR/python.installed" ;;
esac
"""

# `curl ... | sh`: the pipe runs whatever the stub prints, so the stub prints a
# no-op script and applies the "installed" version as a side effect.
_STUB_CURL = f"""#!/usr/bin/env bash
{_STUB_LOG}
if [ -n "${{STUB_NEW_UV:-}}" ]; then printf '%s' "$STUB_NEW_UV" > "$STUB_DIR/uv.version"; fi
echo true
"""

# `runuser -u hermes -- <cmd...>`: drop the first three words and run the command.
_STUB_RUNUSER = f"""#!/usr/bin/env bash
{_STUB_LOG}
[ -z "${{STUB_RUNUSER_FAIL:-}}" ] || exit 126
shift 3
exec "$@"
"""

_STUB_CHOWN = f"#!/usr/bin/env bash\n{_STUB_LOG}\n"

_STUB_ID = f"""#!/usr/bin/env bash
{_STUB_LOG}
[ -z "${{STUB_NO_USER:-}}" ]
"""

# Records whether the live sudoers file already existed when the candidate was checked.
_STUB_VISUDO = f"""#!/usr/bin/env bash
{_STUB_LOG}
if [ -e "$SUDOERS_FILE" ]; then echo "live-file-present" >> "$STUB_LOG"; fi
[ -z "${{STUB_VISUDO_REJECT:-}}" ]
"""

# `install -m MODE -o OWNER -g GROUP SRC DST`: only the last two words matter here.
_STUB_INSTALL = f"""#!/usr/bin/env bash
{_STUB_LOG}
cp "${{@: -2:1}}" "${{@: -1}}"
"""

_FAKE_PYTHON = f"""#!/usr/bin/env bash
echo "Python ${{STUB_PY_REPORT:-{_PYTHON}}}"
"""


def _function(name: str) -> str:
    """Return the source of the top-level bash function ``name`` from the script."""
    text = _SCRIPT.read_text(encoding="utf-8")
    match = re.search(rf"^{name}\(\) \{{\n.*?^\}}\n", text, re.DOTALL | re.MULTILINE)
    assert match is not None, f"function {name} not found in {_SCRIPT.name}"
    return match.group(0)


class Harness:
    """A scratch box: stub binaries, a call log and the globals the guards read."""

    def __init__(self, base: Path) -> None:
        """Create the stub directory and the fake interpreter under ``base``."""
        self.base = base
        self.stub_dir = base / "stubs"
        self.log = base / "calls.log"
        self.python_dir = base / "opt" / "hermes" / "python"
        self.sudoers_dir = base / "etc" / "sudoers.d"
        self.sudoers_file = self.sudoers_dir / "hermes-deploy"
        self.sudoers_dir.mkdir(parents=True)
        write_stubs(
            self.stub_dir,
            {
                "uv": _STUB_UV,
                "curl": _STUB_CURL,
                "runuser": _STUB_RUNUSER,
                "chown": _STUB_CHOWN,
                "id": _STUB_ID,
                "visudo": _STUB_VISUDO,
                "install": _STUB_INSTALL,
            },
        )
        (self.stub_dir / "uv.version").write_text("0.11.0", encoding="utf-8")
        interpreter = self.python_dir / f"cpython-{_PYTHON}-linux" / "bin" / "python3.14"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text(_FAKE_PYTHON, encoding="utf-8", newline="\n")
        interpreter.chmod(0o755)

    def calls(self, command: str | None = None) -> list[str]:
        """Return the logged stub calls, optionally only those of ``command``."""
        if not self.log.exists():
            return []
        lines = self.log.read_text(encoding="utf-8").splitlines()
        return [line for line in lines if command is None or line.startswith(f"{command} ")]

    def run(
        self, body: str, functions: tuple[str, ...], **extra: str
    ) -> subprocess.CompletedProcess[str]:
        """Define ``functions``, then run ``body`` under ``set -euo pipefail``."""
        sources = "\n".join(_function(name) for name in functions)
        script = (
            "set -euo pipefail\n"
            f'PYTHON_DIR="{posix(self.python_dir)}"\n'
            f'SUDOERS_FILE="{posix(self.sudoers_file)}"\n'
            "units_changed=0\n"
            f"{sources}\n{body}\n"
        )
        env = clean_env(
            PATH=f"{posix(self.stub_dir)}{os.pathsep}{os.environ['PATH']}",
            STUB_LOG=posix(self.log),
            STUB_DIR=posix(self.stub_dir),
            SUDOERS_FILE=posix(self.sudoers_file),
            PYTHON_DIR=posix(self.python_dir),
            **extra,
        )
        # A file, not `bash -c`: Windows argv quoting would mangle the script text.
        script_file = self.base / "body.sh"
        script_file.write_text(script, encoding="utf-8", newline="\n")
        return run_bash([posix(script_file)], env)


@pytest.fixture
def box(tmp_path: Path) -> Harness:
    """Return a harness with stubs installed and an empty call log."""
    return Harness(tmp_path)


# --- require_tools ---------------------------------------------------------


def test_require_tools_passes_when_every_tool_exists(box: Harness) -> None:
    """Invariant: with all tools present the pre-flight check is silent and exits 0."""
    result = box.run("require_tools bash uv", ("require_tools",))

    assert result.returncode == 0, output(result)


def test_require_tools_names_every_missing_tool_in_one_failure(box: Harness) -> None:
    """Invariant: a box missing tools fails once, listing all of them, before any step runs."""
    result = box.run("require_tools bash no-such-tool-a no-such-tool-b", ("require_tools",))

    assert result.returncode == 1
    assert "missing tools: no-such-tool-a no-such-tool-b" in result.stdout


# --- ensure_uv -------------------------------------------------------------


def test_uv_at_the_pinned_version_is_not_reinstalled(box: Harness) -> None:
    """Invariant: a box already on the pinned uv never runs the installer."""
    (box.stub_dir / "uv.version").write_text("0.12.5", encoding="utf-8")

    result = box.run("ensure_uv 0.12.5", ("ensure_uv",))

    assert result.returncode == 0, output(result)
    assert box.calls("curl") == []


def test_uv_at_another_version_is_installed_from_the_pinned_url_once(box: Harness) -> None:
    """Invariant: a stale uv is replaced from the versioned URL, and the next run is a no-op."""
    result = box.run("ensure_uv 0.12.5\nensure_uv 0.12.5", ("ensure_uv",), STUB_NEW_UV="0.12.5")

    assert result.returncode == 0, output(result)
    assert box.calls("curl") == ["curl -LsSf https://astral.sh/uv/0.12.5/install.sh"]


def test_uv_install_that_yields_the_wrong_version_fails_the_bootstrap(box: Harness) -> None:
    """Invariant: the install is verified; an installer that leaves another version is fatal."""
    result = box.run("ensure_uv 0.12.5", ("ensure_uv",), STUB_NEW_UV="0.9.9")

    assert result.returncode == 1
    assert "uv reports '0.9.9' after the install, expected 0.12.5" in result.stdout


# --- ensure_python ---------------------------------------------------------


def test_python_already_under_the_install_dir_is_not_reinstalled(box: Harness) -> None:
    """Invariant: a re-dispatch with the pinned Python present never downloads it again."""
    (box.stub_dir / "python.installed").touch()

    result = box.run(f"ensure_python {_PYTHON}", ("ensure_python",))

    assert result.returncode == 0, output(result)
    assert box.calls("uv") == [f"uv python find {_PYTHON}"]


def test_missing_python_is_installed_once_and_verified_as_the_hermes_user(box: Harness) -> None:
    """Invariant: first run installs and verifies Python as hermes; the second is a no-op."""
    body = f"ensure_python {_PYTHON}\nensure_python {_PYTHON}"
    result = box.run(body, ("ensure_python",))

    assert result.returncode == 0, output(result)
    assert box.calls("uv").count(f"uv python install {_PYTHON}") == 1
    runuser_calls = box.calls("runuser")
    assert len(runuser_calls) == 2
    assert all(call.startswith("runuser -u hermes -- ") for call in runuser_calls)
    assert "verified: user hermes runs" in result.stdout


def test_python_resolving_outside_the_install_dir_is_rejected(box: Harness) -> None:
    """Invariant (D-5): an interpreter outside /opt/hermes/python fails under ProtectHome."""
    result = box.run(f"ensure_python {_PYTHON}", ("ensure_python",), STUB_FIND_OUTSIDE="1")

    assert result.returncode == 1
    assert "outside" in result.stdout


def test_python_reporting_another_version_is_rejected(box: Harness) -> None:
    """Invariant: the pin is verified against what the interpreter prints, not what uv says."""
    (box.stub_dir / "python.installed").touch()

    result = box.run(f"ensure_python {_PYTHON}", ("ensure_python",), STUB_PY_REPORT="3.14.2")

    assert result.returncode == 1
    assert f"expected 'Python {_PYTHON}'" in result.stdout


def test_python_the_hermes_user_cannot_run_is_rejected(box: Harness) -> None:
    """Invariant: the service user must be able to run the interpreter, or bootstrap fails."""
    (box.stub_dir / "python.installed").touch()

    result = box.run(f"ensure_python {_PYTHON}", ("ensure_python",), STUB_RUNUSER_FAIL="1")

    assert result.returncode == 126, output(result)


# --- install_units ---------------------------------------------------------


def _unit_dirs(base: Path) -> tuple[Path, Path]:
    """Create a source dir with two units and a stray file, plus an empty target dir."""
    src, dst = base / "units-src", base / "units-dst"
    src.mkdir()
    dst.mkdir()
    (src / "hermes.service").write_text("[Service]\n", encoding="utf-8")
    (src / "hermes-health.timer").write_text("[Timer]\n", encoding="utf-8")
    (src / "hermes.env.example").write_text("HERMES_ENV=prod\n", encoding="utf-8")
    return src, dst


def test_units_are_installed_once_and_a_rerun_changes_nothing(box: Harness) -> None:
    """Invariant (R-5): the second dispatch installs no unit and does not ask for a reload."""
    src, dst = _unit_dirs(box.base)
    first = box.run(
        f'install_units "{posix(src)}" "{posix(dst)}"\necho "changed=$units_changed"',
        ("install_units",),
    )
    installs_after_first = len(box.calls("install"))
    second = box.run(
        f'install_units "{posix(src)}" "{posix(dst)}"\necho "changed=$units_changed"',
        ("install_units",),
    )

    assert "changed=1" in first.stdout
    assert installs_after_first == 2
    assert "changed=0" in second.stdout
    assert len(box.calls("install")) == 2
    assert sorted(p.name for p in dst.iterdir()) == ["hermes-health.timer", "hermes.service"]


def test_only_the_edited_unit_is_reinstalled(box: Harness) -> None:
    """Invariant: a changed unit file is copied again; its unchanged sibling is left alone."""
    src, dst = _unit_dirs(box.base)
    body = f'install_units "{posix(src)}" "{posix(dst)}"'
    box.run(body, ("install_units",))
    (src / "hermes.service").write_text("[Service]\nRestart=on-failure\n", encoding="utf-8")

    box.run(body, ("install_units",))

    installs = box.calls("install")
    assert len(installs) == 3
    assert installs[-1].endswith("hermes.service")
    assert (dst / "hermes.service").read_text(encoding="utf-8") == "[Service]\nRestart=on-failure\n"


# --- ensure_deploy_sudoers -------------------------------------------------

_SUDOERS_FUNCS = ("render_deploy_sudoers", "ensure_deploy_sudoers")
_HEX12 = "[0-9a-f]" * 12


def test_no_deploy_user_writes_no_sudoers_rule(box: Harness) -> None:
    """Invariant: while CD runs as root (DEPLOY_USER unset or root) no sudoers file is created."""
    unset = box.run('ensure_deploy_sudoers ""', _SUDOERS_FUNCS)
    root = box.run("ensure_deploy_sudoers root", _SUDOERS_FUNCS)

    assert unset.returncode == 0, output(unset)
    assert root.returncode == 0, output(root)
    assert list(box.sudoers_dir.iterdir()) == []


def test_sudoers_rule_grants_only_the_cd_commands(box: Harness) -> None:
    """Invariant: the rule lists exactly the deploy entry point and rollback, no bare wildcard."""
    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS)

    assert result.returncode == 0, output(result)
    rules = [
        line
        for line in box.sudoers_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert rules == [
        "ci-deploy ALL=(root) NOPASSWD: /usr/bin/bash "
        f"/var/tmp/hermes-release-{_HEX12}/bin/install_release.sh {_HEX12} "  # noqa: S108
        f"/var/tmp/hermes-release-{_HEX12}",  # noqa: S108
        "ci-deploy ALL=(root) NOPASSWD: /opt/hermes/bin/rollback.sh",
    ]
    assert "*" not in "".join(rules)


@NEEDS_POSIX_MODES
def test_sudoers_file_is_mode_0440(box: Harness) -> None:
    """Invariant: sudo refuses a sudoers file that is writable by anyone; it must be 0440."""
    box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS)

    assert stat.S_IMODE(box.sudoers_file.stat().st_mode) == 0o440


def test_sudoers_candidate_is_validated_before_the_live_file_exists(box: Harness) -> None:
    """Invariant: visudo checks the candidate before a live rule exists; the move publishes it."""
    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS)

    assert result.returncode == 0, output(result)
    visudo_calls = box.calls("visudo")
    assert len(visudo_calls) == 1
    assert visudo_calls[0].startswith("visudo -cf ")
    assert "live-file-present" not in box.calls()
    assert [p.name for p in box.sudoers_dir.iterdir()] == ["hermes-deploy"]


def test_rejected_sudoers_rule_is_never_installed(box: Harness) -> None:
    """Invariant: when visudo rejects the candidate nothing goes live and no temp file stays."""
    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS, STUB_VISUDO_REJECT="1")

    assert result.returncode == 1
    assert "visudo rejected" in result.stdout
    assert list(box.sudoers_dir.iterdir()) == []


def test_rejected_update_keeps_the_previous_sudoers_rule(box: Harness) -> None:
    """Invariant: a bad regenerated rule leaves the last good one in force."""
    box.sudoers_file.write_text("# old good rule\n", encoding="utf-8")

    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS, STUB_VISUDO_REJECT="1")

    assert result.returncode == 1
    assert box.sudoers_file.read_text(encoding="utf-8") == "# old good rule\n"
    assert [p.name for p in box.sudoers_dir.iterdir()] == ["hermes-deploy"]


def test_unchanged_sudoers_rule_is_not_rewritten_or_revalidated(box: Harness) -> None:
    """Invariant (idempotent): a rerun for the same user skips visudo and leaves the file alone."""
    box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS)
    os.utime(box.sudoers_file, (1_700_000_000, 1_700_000_000))

    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS)

    assert result.returncode == 0, output(result)
    assert len(box.calls("visudo")) == 1
    assert box.sudoers_file.stat().st_mtime_ns == 1_700_000_000 * 1_000_000_000


def test_stale_sudoers_rule_is_replaced(box: Harness) -> None:
    """Invariant: a rule for a previous deploy user is overwritten when DEPLOY_USER changes."""
    box.run("ensure_deploy_sudoers old-user", _SUDOERS_FUNCS)

    box.run("ensure_deploy_sudoers new-user", _SUDOERS_FUNCS)

    text = box.sudoers_file.read_text(encoding="utf-8")
    assert "new-user ALL=(root)" in text
    assert "old-user" not in text


@pytest.mark.parametrize("name", ["bad user", "x; ALL=(ALL) ALL", "Root", "-lead", "a,b"])
def test_deploy_user_that_is_not_a_plain_login_name_is_refused(box: Harness, name: str) -> None:
    """Invariant: DEPLOY_USER lands in sudoers, so anything but a plain login name is refused."""
    script = f"ensure_deploy_sudoers '{name}'"
    result = box.run(script, _SUDOERS_FUNCS)

    assert result.returncode == 1
    assert "not a plain login name" in result.stdout
    assert list(box.sudoers_dir.iterdir()) == []


def test_deploy_user_missing_on_the_box_is_refused(box: Harness) -> None:
    """Invariant: a rule for an account that does not exist is a typo, so it fails loudly."""
    result = box.run("ensure_deploy_sudoers ci-deploy", _SUDOERS_FUNCS, STUB_NO_USER="1")

    assert result.returncode == 1
    assert "does not exist" in result.stdout
    assert list(box.sudoers_dir.iterdir()) == []


# --- ensure_auto_reboot ----------------------------------------------------

_AUTO_REBOOT_FUNCS = ("render_auto_reboot", "ensure_auto_reboot")

_STUB_TIMEDATECTL = """\
#!/usr/bin/env bash
echo "${STUB_TZ:-Europe/Madrid}"
"""

_STUB_DPKG_QUERY = """\
#!/usr/bin/env bash
echo "dpkg-query $*" >> "$STUB_LOG"
[ -n "${STUB_UU_INSTALLED:-}" ] && printf 'install ok installed'
exit 0
"""

_STUB_APT_GET = """\
#!/usr/bin/env bash
echo "apt-get $*" >> "$STUB_LOG"
"""

_STUB_SYSTEMCTL = """\
#!/usr/bin/env bash
echo "systemctl $*" >> "$STUB_LOG"
if [ "$1" = is-enabled ]; then
    [ -n "${STUB_UU_ENABLED:-}" ]
    exit
fi
"""


@pytest.fixture
def reboot_box(box: Harness) -> Harness:
    """Return the harness with apt, dpkg, timedatectl and systemctl stubs added."""
    write_stubs(
        box.stub_dir,
        {
            "timedatectl": _STUB_TIMEDATECTL,
            "dpkg-query": _STUB_DPKG_QUERY,
            "apt-get": _STUB_APT_GET,
            "systemctl": _STUB_SYSTEMCTL,
        },
    )
    (box.base / "etc" / "apt" / "apt.conf.d").mkdir(parents=True)
    return box


def _reboot_file(box: Harness) -> Path:
    return box.base / "etc" / "apt" / "apt.conf.d" / "52hermes-auto-reboot"


def _run_auto_reboot(box: Harness, **extra: str) -> subprocess.CompletedProcess[str]:
    body = (
        f'AUTO_REBOOT_FILE="{posix(_reboot_file(box))}"\n'
        "AUTO_REBOOT_TZ=Europe/Madrid\n"
        "ensure_auto_reboot"
    )
    return box.run(body, _AUTO_REBOOT_FUNCS, **extra)


def test_auto_reboot_policy_reboots_at_0330_with_users_logged_in(reboot_box: Harness) -> None:
    """Invariant: the policy file arms the 03:30 reboot, even with SSH sessions open."""
    result = _run_auto_reboot(reboot_box)

    assert result.returncode == 0, output(result)
    policy = _reboot_file(reboot_box).read_text(encoding="utf-8")
    assert 'Unattended-Upgrade::Automatic-Reboot "true";' in policy
    assert 'Unattended-Upgrade::Automatic-Reboot-Time "03:30";' in policy
    assert 'Unattended-Upgrade::Automatic-Reboot-WithUsers "true";' in policy
    assert 'APT::Periodic::Unattended-Upgrade "1";' in policy


def test_auto_reboot_installs_and_enables_unattended_upgrades_when_missing(
    reboot_box: Harness,
) -> None:
    """A fresh box gets the package installed and the service enabled."""
    result = _run_auto_reboot(reboot_box)

    assert result.returncode == 0, output(result)
    assert any("install -y unattended-upgrades" in call for call in reboot_box.calls("apt-get"))
    assert "systemctl enable --now unattended-upgrades.service" in reboot_box.calls("systemctl")


def test_auto_reboot_rerun_changes_nothing(reboot_box: Harness) -> None:
    """Invariant: a re-run on a configured box installs, writes and enables nothing."""
    first = _run_auto_reboot(reboot_box)
    assert first.returncode == 0, output(first)
    mtime = _reboot_file(reboot_box).stat().st_mtime_ns
    reboot_box.log.unlink()

    again = _run_auto_reboot(reboot_box, STUB_UU_INSTALLED="1", STUB_UU_ENABLED="1")

    assert again.returncode == 0, output(again)
    assert _reboot_file(reboot_box).stat().st_mtime_ns == mtime
    assert reboot_box.calls("apt-get") == []
    assert not any(call.startswith("systemctl enable") for call in reboot_box.calls("systemctl"))


def test_auto_reboot_rewrites_a_hand_edited_policy(reboot_box: Harness) -> None:
    """A policy that drifted from the rendered one is replaced."""
    _reboot_file(reboot_box).write_text('Unattended-Upgrade::Automatic-Reboot-Time "12:00";\n')

    result = _run_auto_reboot(reboot_box, STUB_UU_INSTALLED="1", STUB_UU_ENABLED="1")

    assert result.returncode == 0, output(result)
    assert '"03:30"' in _reboot_file(reboot_box).read_text(encoding="utf-8")


def test_auto_reboot_refuses_a_box_on_another_timezone(reboot_box: Harness) -> None:
    """Invariant: 03:30 is only safe in Europe/Madrid; on UTC it would not be armed."""
    result = _run_auto_reboot(reboot_box, STUB_TZ="Etc/UTC")

    assert result.returncode != 0
    assert "Etc/UTC" in output(result)
    assert not _reboot_file(reboot_box).exists()


# --- ensure_journald_retention (#512) ---------------------------------------

_JOURNALD_FUNCS = ("render_journald_dropin", "ensure_journald_retention")


def _dropin(box: Harness) -> Path:
    return box.base / "etc" / "systemd" / "journald.conf.d" / "50-hermes.conf"


def _run_journald(box: Harness) -> subprocess.CompletedProcess[str]:
    body = f'JOURNALD_DROPIN="{posix(_dropin(box))}"\nensure_journald_retention'
    return box.run(body, _JOURNALD_FUNCS)


def test_journald_dropin_carries_the_adr_008_limits(reboot_box: Harness) -> None:
    """Invariant (ADR-008 §1): the journal keeps at most 2G and at most 180 days."""
    result = _run_journald(reboot_box)

    assert result.returncode == 0, output(result)
    lines = [
        line
        for line in _dropin(reboot_box).read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert lines == ["[Journal]", "SystemMaxUse=2G", "MaxRetentionSec=180day"]
    assert reboot_box.calls("systemctl") == ["systemctl restart systemd-journald"]


def test_journald_rerun_restarts_nothing(reboot_box: Harness) -> None:
    """A re-run on a current box writes nothing and leaves journald running."""
    assert _run_journald(reboot_box).returncode == 0
    mtime = _dropin(reboot_box).stat().st_mtime_ns
    reboot_box.log.unlink()

    again = _run_journald(reboot_box)

    assert again.returncode == 0, output(again)
    assert _dropin(reboot_box).stat().st_mtime_ns == mtime
    assert reboot_box.calls("systemctl") == []


def test_journald_rewrites_a_hand_edited_dropin(reboot_box: Harness) -> None:
    """A drop-in that drifted is replaced and journald restarted once."""
    _dropin(reboot_box).parent.mkdir(parents=True)
    _dropin(reboot_box).write_text("[Journal]\nSystemMaxUse=50G\n", encoding="utf-8")

    result = _run_journald(reboot_box)

    assert result.returncode == 0, output(result)
    assert "SystemMaxUse=2G" in _dropin(reboot_box).read_text(encoding="utf-8")
    assert reboot_box.calls("systemctl") == ["systemctl restart systemd-journald"]
