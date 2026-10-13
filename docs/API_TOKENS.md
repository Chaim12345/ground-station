# API tokens

Signed-in users can create and revoke personal API tokens from **API Tokens** in the
sidebar user menu. A token belongs to its creator and is shown only when created.
Create a separate token for each application so access can be revoked independently.

Select only the scopes an application needs. The currently available scopes are:

| Scope | Endpoints |
| --- | --- |
| `station:read` | `GET /api/v1/station` |
| `observations:read` | `GET /api/v1/observations` |
| `artifacts:read` | `GET /api/v1/artifacts`, `GET /api/v1/artifacts/{id}` |

`GET /api/v1/me` checks a token and returns its owner and granted scopes. For
example:

```sh
curl -H 'Authorization: Bearer YOUR_TOKEN' http://localhost:5000/api/v1/me
```

The artifacts endpoints currently provide library metadata. Existing file URLs
in that metadata are browser routes and require a browser login; API token file
downloads will be added with a separate scope. Live events and controls are
also outside this first API slice.

Token management uses the owner's browser login and current password. Its routes
are `GET /api/auth/tokens/scopes`, `GET /api/auth/tokens`,
`POST /api/auth/tokens`, `PATCH /api/auth/tokens/{id}`, and
`DELETE /api/auth/tokens/{id}`. Deletion revokes the token immediately while
retaining its metadata in the list. Scopes are fixed at creation; replace a token
to change its access. Password resets revoke the user's API tokens, and disabled
accounts cannot use them.

API tokens authenticate only the public `/api/v1` routes. They do not open a
browser session or authorize the existing Socket.IO `api.call` interface.
