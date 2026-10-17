#!/bin/sh
# Render the image-owned Nginx template before the backend starts.

http_port="${GS_HTTP_PORT-7000}"

invalid_port() {
    echo "ERROR: GS_HTTP_PORT must be an integer from 1 to 65535 other than 7001"
    exit 1
}

case "$http_port" in
    ''|0*|*[!0-9]*) invalid_port ;;
esac
if [ "${#http_port}" -gt 5 ] || [ "$http_port" -gt 65535 ] ||
   [ "$http_port" -eq 7001 ]; then
    invalid_port
fi

# The template contains only a validated numeric token, so custom host ports do not
# require keeping a second, persistent Nginx configuration in the data volume.
if ! sed "s/@GS_HTTP_PORT@/$http_port/g" \
    /app/ground-station.conf.template > /etc/nginx/conf.d/ground-station.conf; then
    echo "ERROR: Failed to render Nginx configuration"
    exit 1
fi

if ! nginx -t; then
    echo "ERROR: Nginx configuration is invalid"
    exit 1
fi
