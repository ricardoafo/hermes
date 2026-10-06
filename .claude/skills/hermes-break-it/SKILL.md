---
name: hermes-break-it
description: Use before writing code or tests for any hermes change to runtime code, to list how the change can fail, add each way to the failure-mode catalogue, and write a failing test for each.
---

# Break it first

Hermes handles money, a ledger and a live market feed. Most of its defects
were found by asking "how does this fail?" before asking "does this work?".
Do this pass before you write the change, and again before you open the PR.

## 1. List how it can fail

For the code you are about to change, write down every way it can go wrong.
Walk this list and keep every item that applies:

- **Bad input from outside**: NaN, Infinity, huge or negative numbers, a
  wrong type, a missing or extra key, an empty list, unicode, duplicate or
  unsorted rows, a timestamp out of range.
- **A service that fails**: down, slow instead of down, an error page with
  status 200, a rate limit, a partial response.
- **A crash between two writes**: what is on disk if the process dies after
  each write or commit? What does the restart do with it?
- **A restart**: is every piece of state rebuilt (cursors, marks, the open
  position, halts, the stop)? Does the restart redo or skip anything?
- **A second process**: another serve, a reader holding the ledger, two
  appenders on one log.
- **The clock**: jumps forward or back, runs fast, a bar closes late or never.
- **Money edges**: a value exactly at a limit and one just past it, rounding,
  fees, a fill price that differs from the signal price, cash that would go
  negative.
- **Config and deploy**: a wrong path, a missing env var, older code on a newer
  ledger, a changed value after the first boot.

## 2. Check the catalogue

Open `tests/integration/scenarios/failure_modes.py`. For each item:

- Already there and proved: note the test; your change must keep it green.
- Already there as a gap: your change may close it; if so, flip it to
  `proved_by` with the new test.
- Not there: add it. Every entry has exactly one answer:
  - `proved_by`: the node ids of tests that would fail if it broke;
  - `gap`: `issue #N: what happens` (file the issue first);
  - `accepted`: `approved by Rick YYYY-MM-DD: why`.

`tests/integration/scenarios/test_failure_mode_list.py` fails on an entry with
no answer or one that names a test that does not exist.

## 3. Write the failing test first

For each failure mode your change claims to handle, write the test and run it
on the code before your change. It must fail for the right reason. Paste that
red run into your notes; the PR body says it happened.

A known defect you are not fixing gets a strict expected failure, so the day
it is fixed the run turns red and someone flips it:

```python
@pytest.mark.xfail(strict=True, raises=AssertionError, reason="issue #N: what happens")
```

## 4. Say what you found

The PR body keeps the "How does Hermes fail here?" framing: which failure
modes the change handles, which it found and pinned, which issues it filed.
If you found none, say so.

## Rules that still apply

- Behaviour choices on halts, caps, sizing, fees, metrics or kills go to Rick.
  Pin today's behaviour and ask; never change it on your own.
- Synthetic data only. Never use live prices, or any data from 2025-09-01 on.
- No outside project names and no AI attribution (AGENTS.md rules 5 and 11).

See also: `docs/guides/ai-authored-tests.md` (section "Start by asking how it
breaks"), the `hermes-write-tests` and `hermes-scenario` skills.
