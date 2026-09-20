# Existing framework/app versions are retained; only this custom App is added.
FROM frappe/erpnext:v16.35.0@sha256:77eedd6d4180452d1c2832c2f762e588e20de85c038ed8c148aa77007329a8f3
USER frappe
COPY --chown=frappe:frappe . /home/frappe/frappe-bench/apps/meixin_admin
RUN /home/frappe/frappe-bench/env/bin/pip install --no-deps /home/frappe/frappe-bench/apps/meixin_admin
