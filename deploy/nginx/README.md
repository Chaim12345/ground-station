# Nginx Reverse Proxy

The container already runs a small Nginx proxy on port 7000 to serve its startup lobby.
This example adds TLS in front of that public port. The Python backend listens only on
`127.0.0.1:7001` inside the container.

Sample production config:

- `deploy/nginx/ground-station.conf.example`

Before use:

1. Replace `server_name` and certificate paths.
2. Ensure Ground Station is reachable at `127.0.0.1:7000` (or adjust upstream).
3. Keep these forwarded headers:
   - `X-Forwarded-Proto`
   - `X-Forwarded-Host`
   - `X-Forwarded-For`
4. Keep websocket upgrade headers:
   - `Upgrade`
   - `Connection`
5. Keep proxy buffering disabled for large static asset stability:
   - `proxy_buffering off`
   - `proxy_max_temp_file_size 0`

Why these headers matter:

- Ground Station sets `Secure` auth cookies based on request scheme.
- Socket.IO requires websocket upgrade forwarding.
- Authenticated static data routes (`/recordings`, `/decoded`, `/audio`, `/transcriptions`) rely on session cookies reaching the backend.
