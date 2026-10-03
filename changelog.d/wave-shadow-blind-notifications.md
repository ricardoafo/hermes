- **Notifications stay blind until OOS certification**
  (`src/hermes/application/messages/reporting_phase.py`,
  `src/hermes/application/messages/slack.py`,
  `src/hermes/adapters/notifier/slack_adapter.py`): a single `ReportingPhase`
  setting defaults to pre-certification, and under it the Slack daily trade
  digest withholds the winners/losers counts and the per-pair P&L CSV.
  Unblinding is an explicit `CERTIFIED` argument on the Slack adapter that
  logs a warning. Closes #319.
