# API tokens

Signed-in users can create and revoke personal API tokens from **API Tokens** in the
sidebar user menu. A token belongs to its creator and is shown only when created.
Create a separate token for each application so access can be revoked independently.

Select only the scopes an application needs. The currently available scopes are:

| Scope | Endpoints |
| --- | --- |
| `station:read` | `GET /api/v1/station`, `GET /api/v1/status`, `GET /api/v1/events` (status events) |
| `satellites:read` | `GET /api/v1/satellites`, `GET /api/v1/satellites/monitored`, `GET /api/v1/satellites/{norad_id}` |
| `observations:read` | `GET /api/v1/observations`, `GET /api/v1/observations/upcoming`, `GET /api/v1/observations/{id}`, `GET /api/v1/events` (observation events) |
| `artifacts:read` | `GET /api/v1/artifacts`, `GET /api/v1/artifacts/{id}` |

`GET /api/v1/me` checks a token and returns its owner and granted scopes. For
example:

```sh
curl -H 'Authorization: Bearer YOUR_TOKEN' http://localhost:5000/api/v1/me
```

Interactive OpenAPI documentation is available at `/api/docs`; use its Bearer
authorization control to try `/api/v1` requests with a personal API token.

For a Home Assistant REST sensor, `GET /api/v1/status` returns server time,
`observation_state` (`idle` or `observing`), the active observation count, next
scheduled observation time, and enabled monitored-satellite count. This state
describes scheduled observations; manual radio activity may not change it.
Poll it with a token that has `station:read`; it does not require a browser session.

`GET /api/v1/observations/upcoming` returns compact scheduled pass summaries
without hardware configuration or execution logs. The optional `hours` window
is 1–168 (default 24), `limit` is 1–100 (default 25), and `norad_id` filters
to one satellite. `GET /api/v1/satellites/monitored` lists enabled entries by
default and supports `enabled_only=false`, `limit` (1–200), and `offset` for
pagination. `GET /api/v1/satellites` browses the catalog with optional `query`
name search, `limit` (1–100), and `offset`. The full observation endpoints are
intended for custom applications that need the complete schedule data.

Upcoming observations are scheduled jobs, not every predicted satellite pass.
A separate predicted-pass endpoint would need a bounded forecast and protection
against expensive repeated propagation. Another possible addition is scoped
artifact downloads.

`GET /api/v1/events` is a read-only Server-Sent Events (SSE) stream for custom
applications and Home Assistant custom integrations. Use an API token with
`station:read` for `station.status` events, `observations:read` for
`observations.changed` events, or both scopes for both event types:

```sh
curl -N -H 'Authorization: Bearer YOUR_TOKEN' http://localhost:5000/api/v1/events
```

The stream starts with `connected` (`{"replay":false}`) and, for tokens with
`station:read`, a `station.status` snapshot shaped like `GET /api/v1/status`.
It then sends `station.status` when meaningful status fields change and
`observations.changed` after schedule edits or observation status transitions.
An observation event may include `id` and `status`; fetch the REST observation
endpoints for current details. Heartbeat comments keep idle connections open.
The stream has no durable replay or event IDs: on every connection or
reconnection, refresh any observation lists your client needs from REST.
Clients that fall behind are disconnected so they can reconnect and refresh.
Revoked tokens and disabled accounts are rechecked at least every 15 seconds.

Standard Home Assistant REST sensors should continue polling `/api/v1/status`;
consuming SSE requires a custom integration. Browser `EventSource` cannot set
an Authorization header, so browser applications should use a fetch-based SSE
client. Do not put API tokens in URLs. The stream is process-local and requires
a shared event broker before deploying multiple backend workers.

The artifacts endpoints currently provide library metadata. Existing file URLs
in that metadata are browser routes and require a browser login; API token file
downloads will be added with a separate scope. Control endpoints remain outside
this API slice.

Token management uses the owner's browser login over Socket.IO `api.call`.
The commands are `tokens.scopes`, `tokens.list`, `tokens.create`,
`tokens.rename`, and `tokens.revoke`. Creating a token also requires the
current password. Revocation takes effect immediately while
retaining its metadata in the list. Scopes are fixed at creation; replace a token
to change its access. Password resets revoke the user's API tokens, and disabled
accounts cannot use them.

API tokens authenticate only the public `/api/v1` routes. They do not open a
browser session or authorize the existing Socket.IO `api.call` interface.
