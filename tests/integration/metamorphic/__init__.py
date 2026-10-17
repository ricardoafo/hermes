"""Metamorphic relations for the backtest replay (issue #845).

A backtest has no oracle: nobody knows the right P&L of a path. These tests
compare runs with each other instead: two runs whose inputs are related in a
known way must give results related in a known way. Every path is invented by
:mod:`.paths` from a seed; nothing reads a recording.
"""
