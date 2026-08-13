.PHONY: smoke install verify

# Install runtime dependencies (jinja2, PyYAML, requests).
install:
	pip3 install -r requirements.txt

# Run the CI smoke test (no live X session / browser required).
smoke:
	bash tests/run_smoke.sh

# Best-effort verification of fetched X bookmarks.
# Requires the user's logged-in Brave profile (live X session) and cannot run
# on CI, so failures are NOT propagated: never hard-fail CI on this target.
verify:
	@echo "Running verify_x_bookmarks.py (best-effort; requires local X session)..."
	@python3 scripts/verify_x_bookmarks.py || echo "verify skipped: no local X session available"
	@echo "verify done (optional target; result non-fatal)"
