"""``hermes bot``: the read-only Telegram bot; see :mod:`hermes.entrypoints.bot`."""

from __future__ import annotations

from hermes.entrypoints import bot as _impl
from hermes.entrypoints.commands import Command

__all__ = ["COMMAND"]

COMMAND = Command(
    name="bot",
    summary="Run the read-only Telegram bot (hermes-bot.service): /status /money /pos /logs ...",
    run=lambda argv, *, stream: _impl.main(argv, stream=stream),
)
