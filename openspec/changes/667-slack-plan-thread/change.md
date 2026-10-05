# Change: 667-slack-plan-thread

**SDD change**: `667-slack-plan-thread`
**Date**: 2026-10-05
**Status**: Applied
**Tracking issue**: see sibling `issue.md`

Mirror of engram topic key `sdd/667-slack-plan-thread/change` (see
[ADR-009 §6](../../../docs/architecture/adr-009-documentation-strategy.md#6-change-specs--sdd-artifacts-dual-source-convention)
dual-source rule).

## Summary

The approved Slack shape for trade messages is verbose with the plan in a
thread; PR #607 shipped the plan inside the same message. The Slack adapter
now posts the headline lines as the parent message and the plan block (stop,
distance, ATR multiple, the withheld actual) as a reply in that message's
thread. Other families keep one message.

## Scope

### In

- `Note.plan` and `render_parent_and_plan` in `application/messages/shapes.py`;
  the signal notes in `notes.py` move their plan lines out of `verbose`.
- `SlackMessage.plan` (a `SlackReply`) in `application/messages/slack.py`.
- `SlackAdapter.notify` posts the reply with `thread_ts` = the parent's `ts`.
- `hermes preview` prints the thread reply for events that have one.

### Out

- Telegram (compact, one message, unchanged).
- Which family gets which shape: still only the `SHAPES` table.

## Decisions

### D-1: the plan is part of the note, not a Slack-only rule

`Note.plan` holds the plan lines; `render_lines` still renders them after the
verbose lines, so a channel that does not thread sees one message. Slack asks
for the split through `render_parent_and_plan`.

### D-2: a failed reply keeps the parent and raises

The parent is already in the channel and cannot be recalled. The adapter logs
`slack_plan_reply_failed` (channel, thread ts, error type; never the text) and
re-raises the same `SlackNotifierError`. A parent without a `ts` cannot be
threaded and is handled the same way.

## Requirements

### R-1: a signal posts two `chat.postMessage` calls, the second with the first's `ts` as `thread_ts`

### R-2: the parent holds no plan text; the reply holds the plan and the withheld actual

### R-3: a failed parent behaves as before; a failed reply keeps the parent, logs without the text and raises

### R-4: an event with no plan posts once with no `thread_ts`

### R-5: `hermes preview` shows both Slack posts for a signal

## Design

`slack_template` calls `render_parent_and_plan` and wraps the plan lines in a
section block as `SlackMessage.plan`. `SlackAdapter.notify` posts the parent,
then `_post_plan_reply`, then any attachments.

## Test plan

`tests/unit/adapters/notifier/test_slack_plan_thread.py` (respx with recorded
response shapes, syrupy snapshot of both request bodies), `test_slack.py`,
`test_shapes.py`, `test_notes.py`, the Slack snapshots of the two signal
events, and the preview tests.

## Risks

A reply that fails leaves a headline with no plan in the channel; the error is
raised so the router sees it, and the plan is also in the journal event.

## Dependencies on other changes

- PR #607 (notification templates v2).
