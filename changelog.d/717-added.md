- **Console System screen** (tab 7 of `hermes tui`, view only): Services shows
  hermes, the bot, health, daily, backup, archive check and tax pack with their
  systemd state and next timer run; Releases lists the live release and the
  earlier ones, and Enter opens a release with its deploy steps and rollback
  target under `◂ System › Releases › <sha>`; Data and timing shows market
  data, order book and EUR rate freshness and a bar-check time sparkline;
  Machine shows cpu, memory and disk meters, the ledger size and the last
  ledger snapshot. Bootstrap, restart, rollback and a read-only diagnosis
  (`hermes status`) are shown as commands to copy: nothing runs from the
  screen. Over `--vps` it reads the VPS; elsewhere each panel says "not
  available here". Closes #717.
