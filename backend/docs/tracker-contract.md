# Tracker Contract

Status: current

## Scope

Tracking is instance-scoped. Each tracker instance has its own identity and may
own one rotator at a time.

## Core Decisions

1. Tracker identity is mandatory in contracts.
   - `tracker_id` is required on all tracker-related requests/events.

2. Tracking becomes instance-scoped.
   - No contract assumes a single global tracking context.
   - All tracking updates, command statuses, and UI tracker values are tied to a tracker instance.

3. One tracker instance owns one rotator at a time.
   - Ownership conflicts are resolved server-side.
   - A rotator cannot be actively controlled by multiple tracker instances.

4. Frontend target flow is rotator-first.
   - "Set as Target" opens a rotator-selection dialog.
   - Target selection only completes after a rotator is chosen.

## Event Contract

- `satellite-tracking`
  - Required: `tracker_id`
  - Optional payload keys: `tracking_state`, `satellite_data`, `rotator_data`, `rig_data`, `events`

- `ui-tracker-state`
  - Required: `tracker_id`
  - Contains UI selection payload for the corresponding tracker instance

- `tracker-command-status`
  - Required: `tracker_id`, `command_id`, `status`

## API / Handler Contract

- Tracking read/write handlers accept `tracker_id` in request payload.
- Tracking responses include `tracker_id`.
- Event names are unversioned. `tracker_id` distinguishes tracker instances
  inside their payloads; there is no parallel versioned event stream.

## Naming Contract for Persistence

- Canonical tracking-state name format:
  - `satellite-tracking:<tracker_id>`
