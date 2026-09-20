#!/bin/sh
set -eu

nginx-entrypoint.sh &
nginx_pid=$!

while [ ! -s /etc/nginx/conf.d/frappe.conf ]; do
	sleep 0.1
done

# The stock fixed-site template rewrites Origin to the internal site name. Keep
# the browser's public hostname so Frappe's realtime same-origin check succeeds.
sed -i 's#proxy_set_header Origin .*;#proxy_set_header Origin $proxy_x_forwarded_proto://$http_host;#' \
	/etc/nginx/conf.d/frappe.conf
nginx -s reload

wait "$nginx_pid"
