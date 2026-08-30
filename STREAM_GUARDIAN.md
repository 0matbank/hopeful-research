# Stream Guardian

`stream_guardian.py` is an independent stream-health and targeted-repair service. It does not change the movie scanner's watch-button extraction pipeline.

## Schedule

- Main movie scan: twice daily at `0 0,12 * * *` (06:00 and 18:00 Bangladesh time).
- Stream Guardian: every three hours at `17 */3 * * *`.
- A push that changes Guardian/scanner code triggers one cloud `DRY_RUN`; it never writes catalogue data.
- Both workflows use the `movie-catalog-writer` concurrency group with `cancel-in-progress: false`, so catalogue writes never overlap.

## Decision rules

- `200`/`206`, and protected CDN `403`, are treated as alive.
- A link is confirmed dead only when both HEAD and ranged GET return `404`/`410`.
- Timeout and other transient failures are retried within the run and must recur across two runs before targeted repair.
- A systemic transient-failure rate of 10% or more opens the circuit breaker and blocks catalogue changes.
- Only affected source pages are sent to the existing repair/button scanner.
- Fresh links are validated before publication and matched by season, episode, and resolution.

## Preservation

- If one quality is dead but another is live, only the dead quality is removed.
- If every quality is dead and no replacement is available, the full movie record is moved to `history/link_guardian_quarantine.json`; it is not destroyed.
- Quarantined records are retried with exponential backoff. If the old page moved, site search can locate a title-matched replacement page.
- JSON, TXT, M3U, and category history are always regenerated through the shared canonical writer.

## Manual use

```bash
python stream_guardian.py --dry-run --category All
python stream_guardian.py --apply --category "Hindi Movies"
```

Every run writes `guardian-report.json`. GitHub Actions uploads it as a 14-day artifact; the report is not committed to the repository.
