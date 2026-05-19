# FIT Workout Export Roadmap

Tracks `GET /api/v1/users/{user_id}/workouts/{workout_key}/export[.csv|.fit]`
rollout per provider. The endpoint resolves the provider from
`event_record(user_id, external_id)` and dispatches to the corresponding
`WorkoutsTemplate.export_workout_fit` helper. Shared infra: `app/services/fit/`
(fitdecode parser), `app/services/cache/fit_samples.py` (L1 Redis), and
`app/services/storage/raw_fit.py` (L2 S3). Issue: [#1051](https://github.com/the-momentum/open-wearables/issues/1051).

## Status

| Provider | Status | Branch | Notes |
|---|---|---|---|
| Suunto | Shipped on `ow-fixes` | `feat/workout-fit-export` (merged) | Live pull from `/v3/workouts/{workoutKey}/fit`. Verified end-to-end on staging. |
| Garmin | PR ready for review | `feat/garmin-fit-export` | activityFiles PING handler downloads + stores to L2; `/export` reads L2 only. Requires `PERSIST_RAW_FIT=true`. |
| Polar | PR ready for review | `feat/polar-fit-export` | Live pull from `/v3/exercises/{exerciseId}/fit`. Beta endpoint; older M-series watches lack FIT support. |
| Wahoo | Not started | n/a | Requires provider adoption first (OAuth, webhook, workouts service). |
| Coros | Not started | n/a | Requires provider adoption first. |
| Apple, Fitbit, Google, Oura, Samsung, Strava, Ultrahuman, Whoop | Out of scope | n/a | No FIT export on their public APIs. |

## Per-provider notes

### Suunto

Live pull. `SuuntoWorkouts.export_workout_fit` calls
`/v3/workouts/{workoutKey}/fit` via the standard `_make_api_request`
plumbing with `response_format="bytes"`. `event_record.external_id`
stores `workoutKey` (string), so the dispatcher resolves correctly.

### Garmin

Garmin does not expose a pull-FIT-by-id endpoint. The Activity Files
service publishes per-activity FIT files via PING-only notifications
under the `activityFiles` payload key. Each notification carries a
signed callback URL (short TTL) and the partner OAuth 2.0 bearer is
used to fetch the bytes.

The `feat/garmin-fit-export` branch:
- Adds `garmin/handlers/activity_files.py` that downloads + stores L2
  on PING arrival.
- Wires the webhook dispatcher to fan out `activityFiles` items.
- `GarminWorkouts.export_workout_fit` reads L2 only (returns 425 on
  miss, 415 when L2 is disabled).

Deploy requirement: `PERSIST_RAW_FIT=true` (otherwise no FIT survives
the callback URL TTL).

### Polar

Live pull. `PolarWorkouts.export_workout_fit` calls
`/v3/exercises/{exerciseId}/fit`. The hashed `exerciseId` matches
`event_record.external_id`. The endpoint is documented as beta on the
official Polar docs; older M-series watches will respond 404 (the
existing 502 mapping in `api_client` relays the upstream status).

### Wahoo and Coros

Both providers expose FIT downloads on their public APIs (Wahoo Cloud
API: `/v1/workouts/{id}/fit`; Coros Open API: activity file by id).
Neither has a directory under `backend/app/services/providers/` yet.
FIT support is layered on top of provider adoption (OAuth, webhook,
workouts service, models), so do not pre-write FIT helpers. Track
adoption as separate issues; when each provider lands, the FIT layer
is a small per-provider helper + dispatcher entry.

### Providers without FIT

Apple HealthKit, Fitbit, Google Fit, Oura, Samsung Health, Strava
(GPX/TCX only via the JSON API), Ultrahuman, and Whoop do not expose
FIT. `/export` returns 415 for any `event_record` whose
`data_source.provider` is not in `PROVIDERS_WITH_FIT`.
