# API tokens

Signed-in users can create and revoke personal API tokens from **API Tokens** in the
sidebar user menu. A token belongs to its creator and is shown only when created.
Create a separate token for each application so access can be revoked independently.

Select only the scopes an application needs. The currently available scopes are:

| Scope | Endpoints |
| --- | --- |
| `station:read` | `GET /api/v1/station`, `GET /api/v1/status` |
| `satellites:read` | `GET /api/v1/satellites`, `GET /api/v1/satellites/monitored`, `GET /api/v1/satellites/{norad_id}` |
| `observations:read` | `GET /api/v1/observations`, `GET /api/v1/observations/upcoming`, `GET /api/v1/observations/{id}` |
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
against expensive repeated propagation. Other possible additions are scoped
artifact downloads and an event stream for clients that need updates faster
than polling.

The artifacts endpoints currently provide library metadata. Existing file URLs
in that metadata are browser routes and require a browser login; API token file
downloads will be added with a separate scope. Live events and controls are
also outside this first API slice.

Token management uses the owner's browser login over Socket.IO `api.call`.
The commands are `tokens.scopes`, `tokens.list`, `tokens.create`,
`tokens.rename`, and `tokens.revoke`. Creating a token also requires the
current password. Revocation takes effect immediately while
retaining its metadata in the list. Scopes are fixed at creation; replace a token
to change its access. Password resets revoke the user's API tokens, and disabled
accounts cannot use them.

API tokens authenticate only the public `/api/v1` routes. They do not open a
browser session or authorize the existing Socket.IO `api.call` interface.
