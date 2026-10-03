"""Read hermes.service's journal through ``journalctl`` (read-only)."""

from hermes.adapters.systemd_journal.journalctl import JournalctlReader, JournalUnavailableError

__all__ = ["JournalUnavailableError", "JournalctlReader"]
