# E2E Issues

Bugs found by the Playwright suite. Status: `open` (reported to the owner), `fixed` (owner says fixed), `verified` (suite confirms).

| ID | Owner | Spec | Summary | Status |
|---|---|---|---|---|
| T1 | integration-tester | 06-sse-resilience | Test bug: `context.setOffline` doesn't drop an open EventSource in Chromium. The spec now closes the tracked EventSource from the page while stream requests are aborted. | verified |
| T2 | integration-tester | all (docker) | Test infra bug: compose service `app` meant `http://app:8000` was HSTS-upgraded by Chromium (.app TLD), giving ERR_SSL_PROTOCOL_ERROR. Renamed it to `finally-app`. | verified |
| T3 | integration-tester | 07-api portfolio shape | Test bug: total_value vs cash+positions_value can differ by 1 cent from independent rounding. Tolerance set to 1 cent. | verified |

No app bugs found. The full suite (26 tests) passed in docker compose 4 times in a row on 2026-11-13, the last two on the final node:22-slim image after all components were declared ready.
