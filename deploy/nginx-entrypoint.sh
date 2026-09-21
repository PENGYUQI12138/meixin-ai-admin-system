#!/bin/sh
set -eu
: "${SOCKETIO_UPSTREAM_ORIGIN:?Set the internal HTTP origin used by Socket.IO authentication}"
: "${SOCKETIO_UPSTREAM_HOST:?Set the matching internal Host used by Socket.IO authentication}"

# Socket.IO authenticates by calling Frappe through Origin and requires Host to
# match. Use an internal URL reachable from the separate websocket container.
envsubst '${BACKEND}
  ${SOCKETIO}
  ${UPSTREAM_REAL_IP_ADDRESS}
  ${UPSTREAM_REAL_IP_HEADER}
  ${UPSTREAM_REAL_IP_RECURSIVE}
  ${FRAPPE_SITE_NAME_HEADER}
  ${PROXY_READ_TIMEOUT}
  ${CLIENT_MAX_BODY_SIZE}' \
	< /templates/nginx/frappe.conf.template \
	| sed -e "/location \/socket.io {/,/^[[:space:]]*}/ s#proxy_set_header Origin .*#proxy_set_header Origin ${SOCKETIO_UPSTREAM_ORIGIN};#" \
	      -e "/location \/socket.io {/,/^[[:space:]]*}/ s#proxy_set_header Host .*#proxy_set_header Host ${SOCKETIO_UPSTREAM_HOST};#" \
	> /etc/nginx/conf.d/frappe.conf

exec nginx -g 'daemon off;'
