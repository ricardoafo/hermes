"""LabNotebook writer — append-only lab notebook for the Swing MVP1 experiment.

Writes new entries to ``docs/experiments/swing-mvp1/lab-notebook.md`` using
the append-only entry format defined in the notebook's header. Each appended
entry receives a trailing SHA256 comment for integrity verification.

The append-only invariant enforces the anti-p-hacking discipline from
``docs/experiments/swing-mvp1/pre-registration-v1.md`` §12:

    "If a metric result triggers a rules deviation, the deviation MUST be logged
    here BEFORE any code or parameter change."

Guarantees:
    - Never modifies existing content (raises :class:`~hermes.domain.errors.LabNotebookAppendError`
      on duplicate-header attempts).
    - Computes SHA256 of the entry text (UTF-8) and appends it as a Markdown
      comment ``<!-- entry-sha256: <hex> -->`` on the trailing line.
    - Atomic write: builds the full append string in memory, writes once.

Entry format (from lab-notebook.md §Entries header)::

    ### YYYY-MM-DDTHH:MM:SSZ | phase | actor | action

    **Observed metrics** (if applicable):
    - metric_name = value

    **Decision** (if applicable):
    - Decision made

    **Rationale**:
    - Why this decision was made
    - What pre-registration section governs this
    - Whether this counts as a deviation per §11 of pre-registration-v1.md

    **Files touched**:
    - path/to/file (added | modified | frozen)

    <!-- entry-sha256: <hex> -->

Reference:
  docs/experiments/swing-mvp1/lab-notebook.md §Entries.
  pre-registration-v1.md §12.
  spec-9-strategy-development-protocol change.md D-5.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from hermes.domain.errors import LabNotebookAppendError

__all__ = [
    "LabNotebook",
    "LabNotebookEntry",
]

_SHA256_COMMENT_PREFIX = "<!-- entry-sha256: "
_SHA256_COMMENT_SUFFIX = " -->"
_ENTRY = re.compile(r"(?ms)^(### [^\n]*\n(?:(?!^### ).)*?)<!-- entry-sha256: ([0-9a-f]{64}) -->")
# Entries appended before issue #786 were hashed with no blank line after a
# bold label; the blank lines were added by hand afterwards for markdownlint.
_HAND_ADDED_BLANK = re.compile(r"(?m)^(\*\*[^\n]*\*\*:)\n\n(?=- )")


@dataclass(frozen=True, slots=True)
class LabNotebookEntry:
    """Structured representation of a single lab notebook entry.

    All fields correspond to the entry header format required by the notebook.

    Args:
        timestamp_utc: ISO 8601 UTC timestamp string (e.g. ``"2026-08-25T12:48:18Z"``).
        phase: Experiment phase — one of ``pre-registration``, ``data-ingest``,
            ``train``, ``validation``, ``oos``, ``shadow``, ``live``,
            ``post-mortem``, ``meta``.
        actor: Agent that performed the action — one of ``operator``,
            ``claude-experimentalist``, ``claude-integrity-checker``,
            ``engine``, ``external``.
        action: Short description of the action taken.
        observed_metrics: Optional list of ``"metric_name = value"`` strings.
            Use an empty tuple when no metrics are being logged.
        decision: Optional decision text lines. Empty tuple if no decision.
        rationale: Rationale lines (REQUIRED — must document the why, reference
            pre-reg section, and state whether this is a deviation per §11).
        files_touched: Optional list of ``"path/to/file (added | modified | frozen)"``
            strings.

    Reference: docs/experiments/swing-mvp1/lab-notebook.md §Entries header.
    """

    timestamp_utc: str
    phase: str
    actor: str
    action: str
    observed_metrics: tuple[str, ...] = ()
    decision: tuple[str, ...] = ()
    rationale: tuple[str, ...] = ()
    files_touched: tuple[str, ...] = ()

    def header(self) -> str:
        """Return the Markdown heading line for this entry.

        Returns:
            String of the form ``"### YYYY-MM-DDTHH:MM:SSZ | phase | actor | action"``.
        """
        return f"### {self.timestamp_utc} | {self.phase} | {self.actor} | {self.action}"

    def render(self) -> str:
        """Render the entry as a Markdown block (without trailing SHA256 comment).

        Each bold label is followed by a blank line, as markdownlint requires
        before a list, so the entry hashes exactly as it sits in the file.

        Returns:
            Formatted Markdown string. The SHA256 comment is added by
            :class:`LabNotebook` — not here — so the hash covers exactly this
            rendered text.
        """
        lines: list[str] = [self.header(), ""]

        # Observed metrics section
        if self.observed_metrics:
            lines.extend(("**Observed metrics**:", ""))
            lines.extend(f"- {m}" for m in self.observed_metrics)
        else:
            lines.append("**Observed metrics**: N/A (no metrics this entry)")
        lines.append("")

        # Decision section
        if self.decision:
            lines.extend(("**Decision**:", ""))
            lines.extend(f"- {d}" for d in self.decision)
            lines.append("")

        # Rationale section (always present)
        lines.extend(("**Rationale**:", ""))
        if self.rationale:
            lines.extend(f"- {r}" for r in self.rationale)
        else:
            lines.append("- (no rationale provided)")
        lines.append("")

        # Files touched section
        if self.files_touched:
            lines.extend(("**Files touched**:", ""))
            lines.extend(f"- {f}" for f in self.files_touched)
            lines.append("")

        return "\n".join(lines)


class LabNotebook:
    """Append-only writer for the Swing MVP1 lab notebook.

    Appends structured :class:`LabNotebookEntry` instances to the notebook file.
    Each entry is formatted as Markdown, SHA256-checksummed, and appended atomically.

    Raises :class:`~hermes.domain.errors.LabNotebookAppendError` if the same
    entry header already appears as the last entry in the file, preventing
    accidental duplicate entries.

    Args:
        notebook_path: Path to the lab notebook Markdown file. Defaults to
            auto-detected path relative to the project root. Pass explicitly
            in tests.

    Reference:
      pre-registration-v1.md §12.
      spec-9-strategy-development-protocol change.md D-5.
    """

    _DEFAULT_REL_PATH = Path("docs/experiments/swing-mvp1/lab-notebook.md")

    def __init__(self, notebook_path: Path | None = None) -> None:
        """Initialise the writer.

        Args:
            notebook_path: Explicit path to the notebook file. If ``None``,
                auto-detects the project root and uses the default relative path.

        Raises:
            FileNotFoundError: If the notebook file does not exist at the
                resolved path. The file MUST be pre-created (it was initialised
                at pre-registration signing).
        """
        if notebook_path is None:
            notebook_path = self._detect_notebook_path()
        self._path = notebook_path

        if not self._path.exists():
            raise FileNotFoundError(
                f"Lab notebook not found at {self._path}. "
                f"The notebook must be created before appending entries."
            )

    def append(self, entry: LabNotebookEntry) -> str:
        """Append a new entry to the lab notebook.

        Renders the entry, computes its SHA256, and appends both to the file.
        The append is refused if the entry's header already appears as the last
        entry in the file.

        Args:
            entry: The :class:`LabNotebookEntry` to append.

        Returns:
            The SHA256 hex digest of the appended entry text (the exact string
            that is embedded in the ``<!-- entry-sha256: ... -->`` comment).

        Raises:
            LabNotebookAppendError: If the entry header matches the header of
                the last entry already in the file.

        Reference: spec-9-strategy-development-protocol change.md D-5.
        """
        entry_text = entry.render()
        sha256 = hashlib.sha256(entry_text.encode("utf-8")).hexdigest()
        sha256_comment = f"{_SHA256_COMMENT_PREFIX}{sha256}{_SHA256_COMMENT_SUFFIX}"

        self._check_no_duplicate(entry.header())

        append_block = f"\n{entry_text}{sha256_comment}\n"

        with self._path.open("a", encoding="utf-8") as fh:
            fh.write(append_block)

        return sha256

    def last_entry_header(self) -> str | None:
        """Return the header line of the last entry in the notebook, or None.

        Scans the notebook file for the last line starting with ``"### "``.

        Returns:
            The header string (e.g. ``"### 2026-08-25T12:48:18Z | ..."``),
            or ``None`` if no entries exist yet.
        """
        text = self._path.read_text(encoding="utf-8")
        last_header: str | None = None
        for line in text.splitlines():
            if line.startswith("### "):
                last_header = line
        return last_header

    def verify_sha256(self, entry: LabNotebookEntry) -> bool:
        """Verify the SHA256 comment for an entry that exists in the notebook.

        Reads the notebook file and locates the SHA256 comment immediately
        following the entry header. Returns True if the stored hash matches the
        hash of the entry's rendered text.

        Args:
            entry: The :class:`LabNotebookEntry` to verify.

        Returns:
            True if the SHA256 comment in the file matches the re-computed hash
            of the entry text. False if the comment is missing or the hash
            does not match.
        """
        expected_sha = hashlib.sha256(entry.render().encode("utf-8")).hexdigest()
        text = self._path.read_text(encoding="utf-8")
        expected_comment = f"{_SHA256_COMMENT_PREFIX}{expected_sha}{_SHA256_COMMENT_SUFFIX}"
        return expected_comment in text

    def verify_file(self) -> dict[str, bool]:
        """Check every hashed entry against the notebook's own text.

        An entry verifies when the sha256 of its text, from its ``###``
        header up to its ``entry-sha256`` comment, matches the comment. An
        entry written before issue #786 also verifies when the only
        difference is the blank lines added by hand after its bold labels
        and the trailing blank lines.

        Returns:
            Each hashed entry's header line, in file order, mapped to whether
            it verifies.
        """
        text = self._path.read_text(encoding="utf-8")
        results: dict[str, bool] = {}
        for match in _ENTRY.finditer(text):
            body, stored = match.group(1), match.group(2)
            legacy = _HAND_ADDED_BLANK.sub(r"\1\n", body).rstrip("\n") + "\n"
            results[body.split("\n", 1)[0]] = stored in {_sha256(body), _sha256(legacy)}
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_no_duplicate(self, header: str) -> None:
        """Raise if ``header`` matches the last entry header in the file.

        Args:
            header: The entry header line to check.

        Raises:
            LabNotebookAppendError: If the last entry in the file already has
                the same header.
        """
        last = self.last_entry_header()
        if last is not None and last == header:
            raise LabNotebookAppendError(header=header)

    @staticmethod
    def _detect_notebook_path() -> Path:
        """Detect the notebook path from the project root.

        Returns:
            Resolved :class:`Path` to the lab notebook file.

        Raises:
            FileNotFoundError: If no ``pyproject.toml`` is found walking upward
                from this module's directory.
        """
        here = Path(__file__).resolve().parent
        for candidate in [here, *here.parents]:
            if (candidate / "pyproject.toml").exists():
                return candidate / LabNotebook._DEFAULT_REL_PATH
        raise FileNotFoundError(
            f"Could not detect project root: no pyproject.toml found searching upward from {here}."
        )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
