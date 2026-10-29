"""Invariants for the two env templates and the config sections behind them.

``.env.local.example`` (dev) and ``deploy/systemd/hermes.env.example`` (prod)
are what an operator copies. A name in them that no code reads gives false
assurance (a kill switch that does nothing), and a name the config loader
rejects stops ``hermes serve`` at start (issue #1173, audit findings C-1 to C-3).

Reference: the repo audit of 18 Oct 2026, area 04.
"""

import re
from pathlib import Path
from typing import Final

import pytest
from pydantic import BaseModel

from hermes.infrastructure.config.loader import load_config
from hermes.infrastructure.config.root import HermesConfig
from tests.support.paths import REPO_ROOT

_CONFIG_DIR = REPO_ROOT / "config"
_TEMPLATES: Final = (
    REPO_ROOT / ".env.local.example",
    REPO_ROOT / "deploy" / "systemd" / "hermes.env.example",
)
_ASSIGNMENT = re.compile(r"^#?\s*(HERMES_[A-Z0-9_]+)=(\S*)$")
_SECTION_OF_NAME = re.compile(r"^HERMES_([A-Z0-9]+(?:_[A-Z0-9]+)*?)__")
_CONFIG_PACKAGE = REPO_ROOT / "src" / "hermes" / "infrastructure" / "config"


def _names_and_values(template: Path) -> dict[str, str]:
    """Return every ``HERMES_*=value`` line of ``template``, commented out or not."""
    found: dict[str, str] = {}
    for line in template.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            found[match.group(1)] = match.group(2).strip('"')
    return found


def _read_tree(*roots: Path, skip: tuple[Path, ...] = ()) -> str:
    """Concatenate the text of every file under ``roots`` that is not in ``skip``."""
    parts: list[str] = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts or path in skip:
                continue
            try:
                parts.append(path.read_text(encoding="utf-8"))
            except UnicodeDecodeError:
                continue
    return "\n".join(parts)


def _sections() -> dict[str, type[BaseModel]]:
    """Return the config sections of ``HermesConfig``: the fields whose type is a model."""
    return {
        name: field.annotation
        for name, field in HermesConfig.model_fields.items()
        if isinstance(field.annotation, type) and issubclass(field.annotation, BaseModel)
    }


def _section_is_read(section: str, model: type[BaseModel]) -> bool:
    """Return whether code outside the config package uses the section or its model."""
    code = _read_tree(REPO_ROOT / "src", skip=tuple(_CONFIG_PACKAGE.rglob("*")))
    pattern = rf"\.{section}\b|\b{model.__name__}\b|\bload_{section}_config\b"
    return re.search(pattern, code) is not None


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda path: path.name)
def test_every_name_in_a_template_loads_when_uncommented(template: Path) -> None:
    """Uncommenting every assignment of a template still gives a config that loads.

    An unknown ``HERMES_<SECTION>__<FIELD>`` makes ``hermes serve`` exit 3.
    """
    env = _names_and_values(template)
    config = load_config(_CONFIG_DIR, env=env)

    assert config.env == env["HERMES_ENV"]


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda path: path.name)
def test_every_name_in_a_template_is_read_by_code(template: Path) -> None:
    """Each name in a template appears in src, scripts or deploy, or overrides a read section."""
    code = _read_tree(
        REPO_ROOT / "src", REPO_ROOT / "scripts", REPO_ROOT / "deploy", skip=_TEMPLATES
    )
    read_sections = {
        name.upper() for name, model in _sections().items() if _section_is_read(name, model)
    }
    unread: list[str] = []
    for name in _names_and_values(template):
        section = _SECTION_OF_NAME.match(name)
        if name in code or (section and section.group(1) in read_sections):
            continue
        unread.append(name)
    assert unread == []


def test_every_config_section_is_read_or_marked_reserved_and_optional() -> None:
    """A section nobody reads must be optional and say "Reserved", so it can never block startup."""
    offenders: list[str] = []
    for name, model in _sections().items():
        if _section_is_read(name, model):
            continue
        optional = not HermesConfig.model_fields[name].is_required()
        reserved = "Reserved" in (model.__doc__ or "")
        if not (optional and reserved):
            offenders.append(name)
    assert offenders == []
