# Ajar Runbook

Find the code from the Telegram warning or the log (Actions → the run → the `ajar` job).

| Code | What it means | What to do |
|---|---|---|
| `CONFIG_MISSING_SECRET` | A secret is missing (the log names it) | Add it in GitHub Actions secrets with that exact name |
| `CONFIG_BAD_URL` | Supabase URL is wrong | Use `https://YOURPROJECT.supabase.co` |
| `CONFIG_INVALID` | `profile.json` has a mistake | Check commas, quotes and the line in the log |
| `UPSTREAM_AUTH` | A service refused access (log names which) | supabase: use the secret key, run `schema.sql`. telegram: check token, press Start. A job board: it may be blocking GitHub; wait a day |
| `UPSTREAM_REJECTED` | Request refused | supabase: run `schema.sql` again. telegram: check the chat ID |
| `UPSTREAM_DOWN` | A service did not answer | Nothing. It retries next run. If supabase, check the project is not paused |
| `UPSTREAM_429` | Called too often | Wait. Avoid repeated manual runs |
| `CIRCUIT_OPEN` | Calls paused after repeated failures | Nothing. It resets next run |
| `SOURCE_SHAPE_CHANGED` | A job board changed its format | Send the log lines to your developer; update that board's adapter and add a test |
| `SOURCE_EMPTY` | A board returned zero jobs | Wait. If it repeats, treat as a format change |
| `DATA_INVALID` | Unexpected data | Read the `detail` field in the log |
| `TELEGRAM_REJECTED` | One alert could not be sent in any format | Check the chat ID; the job stays waiting |
| `RUN_GAP` | No good run for 18+ hours | Open the Actions tab and read the last failed run |
| `UNEXPECTED_CRASH` / `AJAR_ERROR` / `UPSTREAM_FAILED` | Something unplanned | Send the `stack_trace` line to your developer |

**Normal, not errors:** `source_skipped_rate_budget` (that board was asked recently;
Remotive and Jobicy wait 5 hours, Himalayas 20) and a run that sends no alerts.

## Extreme scenarios

| Scenario | What Ajar does |
|---|---|
| One board down for hours | Others carry on. Warning on the 3rd failure in a row, then every 12th |
| Board changes its format | Detected, warned immediately, other boards unaffected |
| Rate limit or ban | Obeys short waits, gives up on long ones, tries next run |
| Key expired or revoked | No retry. Telegram warning. If the Telegram token itself is dead, the cause is only in the Actions log |
| Database down or paused | Run stops before alerting (so it cannot spam repeats), warns on Telegram, GitHub marks the run failed |
| Database filling up | Old jobs (180 days) and run records (30 days) are deleted daily |
| Duplicates or out-of-order jobs | One fingerprint per company + title. Trade-off: two openings with the same title at one company alert once |
| Clock and timezones | Everything is UTC; dates without a zone are read as UTC |
| Restart mid-run | Safe to rerun. At worst one alert repeats |
| Flood of matches | At most 10 per run; waiting jobs expire after 14 days |
| Runaway or hang | Request timeouts, capped retries and searches, 10-minute job limit, no overlapping runs |
| Need to stop now | Add repository variable `AJAR_PAUSED` = `true` |
| **Schedule stops completely** | Ajar cannot report this itself, because it is not running. **If the daily 💓 report is missing for 2 days, check the Actions tab.** GitHub can delay scheduled runs, and in public repositories disables schedules after 60 days without repository activity (re-enable from the Actions tab) |
