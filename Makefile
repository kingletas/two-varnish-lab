# The gate a change passes before it is committed, and what CI runs.
# Each check needs no cluster: the manifests are rendered, not applied, and the
# two configs are checked by the same Varnish and HAProxy images the lab runs.

VARNISH_IMAGE := varnish:7.7
HAPROXY_IMAGE := haproxy:3.2-alpine

.PHONY: check lint render vcl router

check: lint render vcl router

# The origin and the load, as ruff reads them with its default settings.
lint:
	ruff check server.py load/load.py
	ruff format --check server.py load/load.py

# Every Kustomization builds, so `kubectl apply -k` gets a whole lab and a whole
# load, and the plain-Service load still finds the TARGET it rewrites.
render:
	kubectl kustomize . > /dev/null
	kubectl kustomize load > /dev/null
	kubectl kustomize load-via-service > /dev/null

# varnishd compiles the VCL. The backend's name is resolved at compile time, so
# it is pointed at this container; nothing is fetched from it. `-C` prints the
# generated C, so only a failure's last lines are shown.
vcl:
	@out=$$(docker run --rm --add-host origin:127.0.0.1 \
		-v "$(CURDIR)/default.vcl:/etc/varnish/default.vcl:ro" \
		$(VARNISH_IMAGE) varnishd -C -f /etc/varnish/default.vcl 2>&1) \
		&& echo "default.vcl compiles" \
		|| { printf '%s\n' "$$out" | tail -n 15; exit 1; }

# HAProxy parses its config. The cache addresses are resolved later, at run
# time, so the check needs only the namespace the file names them by.
router:
	docker run --rm -e POD_NAMESPACE=lab \
		-v "$(CURDIR)/haproxy.cfg:/usr/local/etc/haproxy/haproxy.cfg:ro" \
		$(HAPROXY_IMAGE) haproxy -c -f /usr/local/etc/haproxy/haproxy.cfg
