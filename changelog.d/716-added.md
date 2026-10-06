- **Console Logs screen** (`hermes tui`, screen 8): hermes's journal as a
  queryable list. Type a query such as `level>=warn service:engine coin:ETH
  since:7d` (Tab completes fields; `level` compares by severity, debug < info
  < warn < error), see facet counts for level, service and coin, a volume
  histogram coloured by level (`D` `W` `M` `Y` zoom its window) and columns
  sortable by time, level, service, event, coin, details and trace. Enter
  shows the line as pretty JSON beside the list; `C` copies it and `E` exports
  it to `~/hermes-exports/` on the PC (over `--vps` the console says export
  works from the PC). `T` opens every line of that correlation id as a tree
  with timings, and `F` follows the journal live. Nothing from the screen
  goes to any AI or notification. Closes #716.
