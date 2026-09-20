#!/bin/sh
set -eu

# The stock fixed-site template rewrites Origin to the internal site name. Keep
# the browser's public hostname so Frappe's realtime same-origin check succeeds.
envsubst '${BACKEND}
  ${SOCKETIO}
  ${UPSTREAM_REAL_IP_ADDRESS}
  ${UPSTREAM_REAL_IP_HEADER}
  ${UPSTREAM_REAL_IP_RECURSIVE}
  ${FRAPPE_SITE_NAME_HEADER}
  ${PROXY_READ_TIMEOUT}
  ${CLIENT_MAX_BODY_SIZE}' \
	< /templates/nginx/frappe.conf.template \
	| sed 's#proxy_set_header Origin .*;#proxy_set_header Origin $proxy_x_forwarded_proto://$http_host;#' \
	> /etc/nginx/conf.d/frappe.conf

exec nginx -g 'daemon off;'
