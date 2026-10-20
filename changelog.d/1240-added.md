- **Round-trip cap gate row** (`src/hermes/application/calibration/round_trip_cap.py`): every batch candidate signs
  `round_trips_90d_max` (at most 250), the funnel evidence records when each round trip opened, and the generic gate
  fails a Train run when any rolling 90-day window holds more round trips than the cap (ADR-029 §4). The batches
  signed before the row (`swing-b1`, `swing-b2`) are exempt by id and judged as before. Closes #1240.
