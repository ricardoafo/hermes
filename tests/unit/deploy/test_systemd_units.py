"""Invariants for the systemd unit files shipped in ``deploy/systemd/``.

The units are the only thing that starts ``hermes serve`` on the VPS and
nothing in CI starts them. These tests pin the paths and flags the first
real deploy depends on (issue #306).

Reference: openspec/archive/306-serve-systemd-boot/change.md.
"""

import configparser
import io
import shlex
import tomllib
from pathlib import Path, PurePosixPath

import pytest

from hermes.application.supervisor.briefing import SOURCES
from hermes.entrypoints import cli as cli_module

_REPO_ROOT = Path(__file__).resolve().parents[3]
_UNIT_DIR = _REPO_ROOT / "deploy" / "systemd"
_UNIT_FILES = sorted([*_UNIT_DIR.glob("*.service"), *_UNIT_DIR.glob("*.timer")])
_SERVICE_FILES = [path for path in _UNIT_FILES if path.suffix == ".service"]

_ENV_FILE = "/etc/hermes/env.production"
_STAGING_UNIT = "hermes-staging.service"
_SHADOW_UNIT = "hermes-shadow@.service"
_OWN_ENV_FILES = {
    "hermes-lab-memory.service": "/etc/hermes/env.staging",
    "hermes-bot.service": "/etc/hermes/env.bot",
    _STAGING_UNIT: "/etc/hermes/env.staging",
    _SHADOW_UNIT: "/etc/hermes/env.staging",
}
_NO_ENV_FILE = {"hermes-brief.service"}
_EXEC_PREFIXES = "@-:+!"


def _parse(name: str) -> configparser.ConfigParser:
    """Parse one unit file; ``strict=False`` because systemd allows repeated keys."""
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    parser.read_string((_UNIT_DIR / name).read_text(encoding="utf-8"))
    return parser


def _hermes_exec_argv() -> list[str]:
    """Return the ``hermes.service`` ExecStart split into argv tokens."""
    return shlex.split(_parse("hermes.service")["Service"]["ExecStart"])


def _flag(argv: list[str], name: str) -> str:
    """Return the value that follows ``name`` in ``argv``."""
    return argv[argv.index(name) + 1]


def test_all_expected_unit_files_are_discovered() -> None:
    """Parametrised tests below never silently run on an empty file set."""
    names = {path.name for path in _UNIT_FILES}
    assert names >= {
        "hermes.service",
        "hermes-backup.service",
        "hermes-health.service",
        "hermes-backup.timer",
        "hermes-health.timer",
        "hermes-archive-check.service",
        "hermes-archive-check.timer",
        _STAGING_UNIT,
    }


@pytest.mark.parametrize("path", _SERVICE_FILES, ids=lambda p: p.name)
def test_service_exec_start_is_an_absolute_path(path: Path) -> None:
    """A relative ExecStart binary fails at start with 203/EXEC under systemd."""
    exec_start = _parse(path.name)["Service"]["ExecStart"]
    binary = exec_start.split()[0].lstrip(_EXEC_PREFIXES)
    assert PurePosixPath(binary).is_absolute(), f"{path.name} ExecStart binary {binary!r}"


def test_hermes_service_runs_the_serve_subcommand_of_the_console_script() -> None:
    """The unit starts `hermes serve` from the active release's virtualenv."""
    argv = _hermes_exec_argv()
    assert argv[:2] == ["/opt/hermes/current/.venv/bin/hermes", "serve"]


def test_hermes_service_binary_is_a_declared_console_script() -> None:
    """ExecStart cannot drift from the `[project.scripts]` table in pyproject.toml."""
    pyproject = tomllib.loads((_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    binary = PurePosixPath(_hermes_exec_argv()[0]).name
    assert binary in pyproject["project"]["scripts"], f"{binary!r} is not a console script"


def test_hermes_service_passes_absolute_config_dir_and_db_paths() -> None:
    """Relative CLI defaults must never decide where the VPS reads or writes."""
    argv = _hermes_exec_argv()
    assert _flag(argv, "--config-dir") == "/opt/hermes/current/config"
    assert _flag(argv, "--db") == "/var/lib/hermes/hermes.sqlite3"


def test_hermes_service_flags_are_accepted_by_the_real_cli_parser() -> None:
    """An unknown flag in ExecStart would crash-loop; the real parser must accept them."""
    out = io.StringIO()
    rc = cli_module.main([*_hermes_exec_argv()[1:], "--help"], stream=out)
    assert rc == 0, out.getvalue()


@pytest.mark.parametrize("path", _SERVICE_FILES, ids=lambda p: p.name)
def test_service_environment_file_is_the_bootstrap_path(path: Path) -> None:
    """Every unit that loads secrets reads its own tier's file, with no '-'.

    A leading '-' would boot the process with no secrets instead of failing the start.
    The bot reads its own bootstrap-written file so it never sees exchange keys
    (ADR-015 D-05); the staging unit reads the one the operator writes by hand
    (#192); every other unit reads env.production. The supervisor desk's brief
    unit reads none: the desk never loads the env file (ADR-012 D-07, #897).
    """
    service = _parse(path.name)["Service"]
    if path.name in _NO_ENV_FILE:
        assert "EnvironmentFile" not in service
    else:
        assert service["EnvironmentFile"] == _OWN_ENV_FILES.get(path.name, _ENV_FILE)


def test_hermes_service_loads_the_environment_file() -> None:
    """The engine must not start without the secrets file (no EnvironmentFile= means no secrets)."""
    assert "EnvironmentFile" in _parse("hermes.service")["Service"]


def test_hermes_service_runs_as_prod_when_the_environment_file_omits_hermes_env() -> None:
    """A missing HERMES_ENV must not start the engine as dev, past the prod channel check."""
    assert _parse("hermes.service")["Service"]["Environment"] == "HERMES_ENV=prod"


def test_hermes_service_keeps_the_strict_filesystem_sandbox() -> None:
    """Loosening ProtectSystem would widen the blast radius of a compromised process."""
    assert _parse("hermes.service")["Service"]["ProtectSystem"] == "strict"


def test_hermes_service_db_directory_is_covered_by_read_write_paths() -> None:
    """With ProtectSystem=strict the DB directory is read-only unless listed here."""
    service = _parse("hermes.service")["Service"]
    db_dir = PurePosixPath(_flag(_hermes_exec_argv(), "--db")).parent
    writable = [PurePosixPath(p) for p in service["ReadWritePaths"].split()]
    assert any(db_dir == path or path in db_dir.parents for path in writable), (
        f"{db_dir} is not under ReadWritePaths {writable}"
    )


def test_hermes_service_working_directory_is_the_writable_state_directory() -> None:
    """A relative write must land in state, not in the read-only release."""
    service = _parse("hermes.service")["Service"]
    assert service["WorkingDirectory"] == "/var/lib/hermes"


def test_hermes_service_state_directory_is_created_by_systemd() -> None:
    """StateDirectory= avoids a 226/NAMESPACE failure when /var/lib/hermes is missing."""
    assert _parse("hermes.service")["Service"]["StateDirectory"] == "hermes"


def test_hermes_service_does_not_restart_on_argument_or_config_errors() -> None:
    """Exit 2 (usage) and 3 (config load failed) are deterministic: no restart loop."""
    service = _parse("hermes.service")["Service"]
    assert set(service["RestartPreventExitStatus"].split()) >= {"2", "3"}


def _seconds(value: str) -> int:
    """Convert a systemd time span such as ``30s`` or ``10min`` to seconds."""
    for suffix, factor in (("min", 60), ("s", 1)):
        if value.endswith(suffix):
            return int(value.removesuffix(suffix)) * factor
    return int(value)


def test_hermes_service_declares_a_finite_start_limit() -> None:
    """Without StartLimit* a crash loop restarts for ever (and re-fetches history each time)."""
    unit = _parse("hermes.service")["Unit"]
    assert 1 <= int(unit["StartLimitBurst"]) <= 10
    assert 0 < _seconds(unit["StartLimitIntervalSec"]) <= 3600


def test_hermes_service_start_limit_trips_inside_its_interval() -> None:
    """A burst that takes longer than the interval to reach never trips the limit."""
    parser = _parse("hermes.service")
    burst = int(parser["Unit"]["StartLimitBurst"])
    interval = _seconds(parser["Unit"]["StartLimitIntervalSec"])
    assert burst * _seconds(parser["Service"]["RestartSec"]) < interval


@pytest.mark.parametrize("path", _UNIT_FILES, ids=lambda p: p.name)
def test_unit_values_carry_no_inline_comments(path: Path) -> None:
    """A trailing `# ...` makes a unit value unparsable: units have no inline comments."""
    parser = _parse(path.name)
    offenders = [
        f"[{section}] {key}={value}"
        for section in parser.sections()
        for key, value in parser.items(section)
        if " #" in value
    ]
    assert offenders == []


@pytest.mark.parametrize("path", _UNIT_FILES, ids=lambda p: p.name)
@pytest.mark.parametrize(
    "legacy",
    ["/etc/hermes.env", "/opt/hermes/.venv", "/opt/hermes/deploy/", "-m hermes serve"],
)
def test_unit_files_never_reference_the_pre_release_layout(path: Path, legacy: str) -> None:
    """The single-venv layout and the flat env file are gone; no unit may name them."""
    assert legacy not in path.read_text(encoding="utf-8")


_RELEASE_DEPLOY_DIR = PurePosixPath("/opt/hermes/current/deploy")
_HERMES_BIN_DIR = PurePosixPath("/opt/hermes/bin")
_NOTIFIER_TEMPLATE = "hermes-notify-failure@.service"


def _exec_argv_of(unit_name: str, key: str) -> list[str]:
    """Return the ``key`` command line of ``unit_name``'s [Service] as argv tokens."""
    return shlex.split(_parse(unit_name)["Service"][key])


def _exec_argv(unit_name: str) -> list[str]:
    """Return the ExecStart of ``unit_name`` split into argv tokens."""
    return shlex.split(_parse(unit_name)["Service"]["ExecStart"])


def test_hermes_service_starts_the_failure_notifier_when_it_fails() -> None:
    """A tripped start limit or an exit 2/3 must reach the operator, not stay in the journal."""
    assert _parse("hermes.service")["Unit"]["OnFailure"] == "hermes-notify-failure@%N.service"


def test_on_failure_instance_name_drops_the_service_suffix() -> None:
    """%n would name the alert `hermes-notify-failure@hermes.service.service` (#364)."""
    assert "%n" not in _parse("hermes.service")["Unit"]["OnFailure"]


def test_hermes_service_declares_on_failure_in_the_unit_section() -> None:
    """Systemd ignores OnFailure= under [Service], so the alert would never fire."""
    assert "OnFailure" not in _parse("hermes.service")["Service"]


def test_on_failure_instance_resolves_to_a_shipped_template_unit() -> None:
    """OnFailure= naming a template that is not in deploy/systemd/ would alert nobody."""
    target = _parse("hermes.service")["Unit"]["OnFailure"]
    assert target.endswith("@%N.service")
    template = target.removesuffix("%N.service") + ".service"
    assert (_UNIT_DIR / template).is_file(), f"{template} is not shipped"


def test_hermes_service_posts_a_crash_notice_after_every_unclean_stop() -> None:
    """A crash Restart= recovers from never reaches OnFailure=; ExecStopPost= covers it (#380)."""
    assert _exec_argv_of("hermes.service", "ExecStopPost") == [
        "-+/bin/bash",
        str(_HERMES_BIN_DIR / "hermes-notify-crash.sh"),
        "%n",
    ]
    assert (_REPO_ROOT / "deploy" / "hermes-notify-crash.sh").is_file()


def test_failure_notifier_is_a_oneshot() -> None:
    """The notifier runs once per failure and exits; a long-running unit would never finish."""
    assert _parse(_NOTIFIER_TEMPLATE)["Service"]["Type"] == "oneshot"


def test_failure_notifier_never_restarts() -> None:
    """One failure, one message: any Restart= policy would repeat the alert."""
    assert _parse(_NOTIFIER_TEMPLATE)["Service"].get("Restart", "no") == "no"


def test_failure_notifier_loads_the_credentials_from_the_environment_file() -> None:
    """The Telegram token and chat id come from env.production, never from the unit file."""
    assert _parse(_NOTIFIER_TEMPLATE)["Service"]["EnvironmentFile"] == _ENV_FILE


def test_failure_notifier_hands_the_failed_unit_name_to_its_script() -> None:
    """The message names the unit that failed, so the instance name must reach the script."""
    assert _exec_argv(_NOTIFIER_TEMPLATE)[-1] == "%i.service"


def test_failure_notifier_runs_its_script_from_outside_the_release() -> None:
    """A missing or broken `current` is why hermes fails; the alert must not depend on it (#364)."""
    script = PurePosixPath(_exec_argv(_NOTIFIER_TEMPLATE)[1])
    assert script == _HERMES_BIN_DIR / "hermes-notify-failure.sh"
    assert (_REPO_ROOT / "deploy" / script.name).is_file()


def test_health_service_loads_the_ping_url_from_the_environment_file() -> None:
    """The ping URL lives in env.production; without it the ping has no target."""
    assert _parse("hermes-health.service")["Service"]["EnvironmentFile"] == _ENV_FILE


def test_health_ping_script_reads_the_variable_the_bootstrap_workflow_writes() -> None:
    """The ping URL variable must be one vps-bootstrap.yml renders into env.production."""
    variable = "HERMES_HEALTHCHECKS__PING_URL"
    script = (_REPO_ROOT / "deploy" / "hermes-health-ping.sh").read_text(encoding="utf-8")
    workflow = (_REPO_ROOT / ".github" / "workflows" / "vps-bootstrap.yml").read_text(
        encoding="utf-8"
    )
    assert f"${{{variable}:-}}" in script
    assert f"{variable}:" in workflow


@pytest.mark.parametrize(
    "path",
    [_UNIT_DIR / "hermes-health.service", _REPO_ROOT / "deploy" / "hermes-health-ping.sh"],
    ids=lambda p: p.name,
)
def test_health_ping_never_falls_back_to_a_placeholder_url(path: Path) -> None:
    """An unset URL must fail loudly; pinging the all-zero UUID only ever 404s unnoticed."""
    text = path.read_text(encoding="utf-8")
    assert "00000000-0000-0000-0000-000000000000" not in text
    assert "${HEALTHCHECK_URL" not in text


def test_health_service_pings_through_the_conditional_script_only() -> None:
    """A direct curl in the unit would ping even while hermes.service is down."""
    assert _exec_argv("hermes-health.service") == [
        "/bin/bash",
        str(_RELEASE_DEPLOY_DIR / "hermes-health-ping.sh"),
    ]


def test_health_script_is_shipped_in_the_release_deploy_directory() -> None:
    """ExecStart follows `current/deploy/`; a script missing from repo deploy/ fails with 127."""
    script = PurePosixPath(_exec_argv("hermes-health.service")[1])
    assert script.parent == _RELEASE_DEPLOY_DIR
    assert (_REPO_ROOT / "deploy" / script.name).is_file()


def test_daily_timer_fires_at_seven_madrid_time() -> None:
    """The alive line lands at 07:00 Madrid, following the CET/CEST switch (#373)."""
    timer = _parse("hermes-daily.timer")["Timer"]

    assert (timer["OnCalendar"], timer["Persistent"], timer["Unit"]) == (
        "*-*-* 07:00:00 Europe/Madrid",
        "true",
        "hermes-daily.service",
    )


def test_daily_service_is_a_oneshot_with_journal_access_and_state() -> None:
    """It runs once, reads the journal for the counts and keeps yesterday's NRestarts."""
    service = _parse("hermes-daily.service")["Service"]

    assert (
        service["Type"],
        service["SupplementaryGroups"],
        service["StateDirectory"],
        service["EnvironmentFile"],
    ) == ("oneshot", "systemd-journal", "hermes-daily", _ENV_FILE)


def test_daily_service_runs_the_shipped_script_from_the_release() -> None:
    """ExecStart follows `current/deploy/`; the script must exist in repo deploy/."""
    script = PurePosixPath(_exec_argv("hermes-daily.service")[1])

    assert script == _RELEASE_DEPLOY_DIR / "hermes-daily-line.sh"
    assert (_REPO_ROOT / "deploy" / script.name).is_file()


def test_tax_pack_timer_fires_at_half_past_seven_madrid_time() -> None:
    """The packs refresh daily at 07:30 Madrid, after the alive line (#568, R-6)."""
    timer = _parse("hermes-tax-pack.timer")["Timer"]

    assert (timer["OnCalendar"], timer["Persistent"], timer["Unit"]) == (
        "*-*-* 07:30:00 Europe/Madrid",
        "true",
        "hermes-tax-pack.service",
    )


def test_tax_pack_service_writes_only_to_its_state_directory() -> None:
    """The pack folder is the unit's private state directory; nothing else is writable."""
    service = _parse("hermes-tax-pack.service")["Service"]
    argv = shlex.split(service["ExecStart"])

    assert (
        service["Type"],
        service["StateDirectory"],
        service["StateDirectoryMode"],
        service["ReadWritePaths"],
        _flag(argv, "--out"),
    ) == ("oneshot", "hermes-tax-pack", "0700", "", "/var/lib/hermes-tax-pack")


def test_tax_pack_service_runs_tax_pack_from_the_release() -> None:
    """ExecStart runs `hermes tax-pack` from the active release, on the serve ledger."""
    argv = shlex.split(_parse("hermes-tax-pack.service")["Service"]["ExecStart"])

    assert argv[:2] == ["/opt/hermes/current/.venv/bin/hermes", "tax-pack"]
    assert _flag(argv, "--db") == _flag(_hermes_exec_argv(), "--db")


def test_tax_pack_flags_are_accepted_by_the_real_cli_parser(tmp_path: Path) -> None:
    """The unit's flags parse; with no ledger at the server path the run stops at exit 1."""
    argv = shlex.split(_parse("hermes-tax-pack.service")["Service"]["ExecStart"])[1:]
    argv[argv.index("--out") + 1] = str(tmp_path / "packs")
    argv[argv.index("--config-dir") + 1] = str(_REPO_ROOT / "config")
    argv[argv.index("--db") + 1] = str(tmp_path / "missing.sqlite3")
    stream, err = io.StringIO(), io.StringIO()

    assert cli_module.main(argv, stream=stream, err=err) == 1
    assert "no ledger at" in err.getvalue()


_PROD_PATHS = ("/opt/hermes", "/var/lib/hermes", "/etc/hermes/env.production")


def _staging_text_without_comments() -> str:
    """Return the staging unit's directives, comment lines dropped."""
    text = (_UNIT_DIR / _STAGING_UNIT).read_text(encoding="utf-8")
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("prod_path", _PROD_PATHS)
def test_staging_service_names_no_prod_path(prod_path: str) -> None:
    """A staging unit that named a prod path could read or write prod state (#192, A-3.1)."""
    words = _staging_text_without_comments().replace("=", " ").split()
    offenders = [word for word in words if word == prod_path or word.startswith(f"{prod_path}/")]
    assert offenders == []


def test_staging_service_never_pages() -> None:
    """Staging failures stay in the journal: no OnFailure= or ExecStopPost= alert (#192)."""
    parser = _parse(_STAGING_UNIT)
    keys = {key for section in parser.sections() for key in parser[section]}
    assert keys & {"OnFailure", "ExecStopPost"} == set()


def test_staging_service_runs_as_its_own_user_on_its_own_state() -> None:
    """Isolation is by the OS (D-2): its own user, state dir, cwd and writable path."""
    service = _parse(_STAGING_UNIT)["Service"]
    assert (
        service["User"],
        service["Group"],
        service["StateDirectory"],
        service["WorkingDirectory"],
        service["ReadWritePaths"],
    ) == (
        "hermes-staging",
        "hermes-staging",
        "hermes-staging",
        "/var/lib/hermes-staging",
        "/var/lib/hermes-staging",
    )


def test_staging_service_serves_its_own_release_and_ledger() -> None:
    """ExecStart runs the staging release against the staging ledger, never prod's."""
    argv = _exec_argv(_STAGING_UNIT)
    assert argv[:2] == ["/opt/hermes-staging/current/.venv/bin/hermes", "serve"]
    assert _flag(argv, "--config-dir") == "/opt/hermes-staging/current/config"
    assert _flag(argv, "--db") == "/var/lib/hermes-staging/hermes.sqlite3"


def test_staging_service_flags_are_accepted_by_the_real_cli_parser() -> None:
    """An unknown flag in the staging ExecStart would crash-loop it too."""
    out = io.StringIO()
    rc = cli_module.main([*_exec_argv(_STAGING_UNIT)[1:], "--help"], stream=out)
    assert rc == 0, out.getvalue()


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("Unit", "StartLimitIntervalSec"),
        ("Unit", "StartLimitBurst"),
        ("Service", "RestartPreventExitStatus"),
        ("Service", "NoNewPrivileges"),
        ("Service", "PrivateTmp"),
        ("Service", "ProtectSystem"),
        ("Service", "ProtectHome"),
    ],
)
def test_staging_service_keeps_the_prod_hardening_and_crash_guard(section: str, key: str) -> None:
    """Staging shares the box with prod; it must be sandboxed and loop-limited the same way."""
    assert _parse(_STAGING_UNIT)[section][key] == _parse("hermes.service")[section][key]


def test_staging_service_logs_under_its_own_identifier() -> None:
    """`journalctl -t hermes-staging` must never mix in prod lines."""
    assert _parse(_STAGING_UNIT)["Service"]["SyslogIdentifier"] == "hermes-staging"


def test_bot_unit_loads_only_its_own_env_file() -> None:
    """ADR-015 D-05: the bot never sees env.production and its exchange keys."""
    service = _parse("hermes-bot.service")["Service"]

    assert service["EnvironmentFile"] == "/etc/hermes/env.bot"


def test_bot_unit_runs_hermes_bot_read_only_on_the_ledger() -> None:
    """The bot runs `hermes bot` and gets no write path on the state dir."""
    service = _parse("hermes-bot.service")["Service"]

    assert shlex.split(service["ExecStart"])[:2] == ["/opt/hermes/current/.venv/bin/hermes", "bot"]
    assert (service["ReadOnlyPaths"], service.get("ReadWritePaths")) == ("/var/lib/hermes", None)


def test_brief_timer_fires_at_a_quarter_past_seven_madrid_time() -> None:
    """The desk's brief is kept daily at 07:15 Madrid, between the alive line and the tax pack."""
    timer = _parse("hermes-brief.timer")["Timer"]

    assert (timer["OnCalendar"], timer["Persistent"], timer["Unit"]) == (
        "*-*-* 07:15:00 Europe/Madrid",
        "true",
        "hermes-brief.service",
    )


def test_brief_service_loads_no_env_file_and_writes_only_the_ledger_directory() -> None:
    """The desk never reads the env file (ADR-012 D-07); the archive sits beside the ledger."""
    service = _parse("hermes-brief.service")["Service"]
    argv = shlex.split(service["ExecStart"])

    assert "EnvironmentFile" not in service
    assert (service["Type"], service["ReadWritePaths"], service["ProtectSystem"]) == (
        "oneshot",
        "/var/lib/hermes",
        "strict",
    )
    assert _flag(argv, "--db") == _flag(_hermes_exec_argv(), "--db")


def test_brief_service_flags_are_accepted_by_the_real_supervisor(tmp_path: Path) -> None:
    """The unit keeps the brief with the six source grants the Briefs screen reads."""
    argv = shlex.split(_parse("hermes-brief.service")["Service"]["ExecStart"])[1:]
    argv[argv.index("--config-dir") + 1] = str(_REPO_ROOT / "config")
    argv[argv.index("--db") + 1] = str(tmp_path / "missing.sqlite3")

    grants = {argv[i + 1] for i, flag in enumerate(argv) if flag == "--grant"}
    assert grants == {f"hermes:read:{name}" for name in SOURCES}
    assert cli_module.main(argv, stream=io.StringIO(), err=io.StringIO()) == 0


def _shadow_argv(name: str) -> list[str]:
    """Return the shadow template's ExecStart for instance ``name``, as systemd expands it."""
    return shlex.split(_parse(_SHADOW_UNIT)["Service"]["ExecStart"].replace("%i", name))


def test_shadow_template_runs_the_instance_strategy_on_its_own_ledger() -> None:
    """#1076: instance <name> serves strategy <name> on a ledger no other instance names."""
    argv = _shadow_argv("swing-donchian-4h")
    assert (
        argv[:2],
        _flag(argv, "--config-dir"),
        _flag(argv, "--strategy"),
        _flag(argv, "--db"),
    ) == (
        ["/opt/hermes-staging/current/.venv/bin/hermes", "serve"],
        "/opt/hermes-staging/current/config",
        "swing-donchian-4h",
        "/var/lib/hermes-staging/shadows/swing-donchian-4h/hermes.sqlite3",
    )


def test_shadow_template_can_write_only_its_own_ledger_directory() -> None:
    """#1076: the state dir, cwd and only writable path are the instance's ledger directory."""
    service = _parse(_SHADOW_UNIT)["Service"]
    ledger_dir = str(PurePosixPath(_flag(_shadow_argv("%i"), "--db")).parent)
    assert (
        "/var/lib/" + service["StateDirectory"],
        service["WorkingDirectory"],
        service["ReadWritePaths"],
    ) == (ledger_dir, ledger_dir, ledger_dir)


def test_shadow_template_flags_are_accepted_by_the_real_cli_parser() -> None:
    """An unknown flag in the template's ExecStart would crash-loop every shadow."""
    out = io.StringIO()
    rc = cli_module.main([*_shadow_argv("swing-donchian-4h")[1:], "--help"], stream=out)
    assert rc == 0, out.getvalue()


def test_shadow_template_never_pages_and_names_no_prod_path() -> None:
    """A lab shadow stays in the journal and never touches prod's code, state or secrets."""
    parser = _parse(_SHADOW_UNIT)
    keys = {key for section in parser.sections() for key in parser[section]}
    text = (_UNIT_DIR / _SHADOW_UNIT).read_text(encoding="utf-8")
    words = (
        " ".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
        .replace("=", " ")
        .split()
    )
    prod = [w for w in words for p in _PROD_PATHS if w == p or w.startswith(f"{p}/")]
    assert (keys & {"OnFailure", "ExecStopPost"}, prod) == (set(), [])


@pytest.mark.parametrize(
    ("section", "key"),
    [
        ("Unit", "StartLimitIntervalSec"),
        ("Unit", "StartLimitBurst"),
        ("Service", "User"),
        ("Service", "RestartPreventExitStatus"),
        ("Service", "NoNewPrivileges"),
        ("Service", "PrivateTmp"),
        ("Service", "ProtectSystem"),
        ("Service", "ProtectHome"),
    ],
)
def test_shadow_template_keeps_the_staging_user_hardening_and_crash_guard(
    section: str, key: str
) -> None:
    """Each shadow runs as the lab user, sandboxed and loop-limited like the staging serve."""
    assert _parse(_SHADOW_UNIT)[section][key] == _parse(_STAGING_UNIT)[section][key]


@pytest.mark.parametrize("name", ["hermes.service", _STAGING_UNIT, _SHADOW_UNIT])
def test_serve_starts_after_the_clock_is_synced(name: str) -> None:
    """Serve tells missed bars from live ones by the clock, so it waits for time sync (#1078).

    A clock still wrong at boot would mark live bars as missed and pass over
    their entries, or treat missed bars as live and fill them back-dated.
    """
    unit = _parse(name)["Unit"]
    assert (
        "time-sync.target" in unit["After"].split(),
        "time-sync.target" in unit["Wants"].split(),
    ) == (
        True,
        True,
    )
