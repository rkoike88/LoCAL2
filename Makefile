.PHONY: dist install dev test clean harness

# Build the React frontend and copy the output into the Python package data
# directory so that 'pip install local2' serves the bundled UI.
dist:
	cd frontend && npm run build
	rm -rf src/local/api/static
	cp -r frontend/dist src/local/api/static
	@echo "[make dist] Frontend built → src/local/api/static/"

# Install the package in editable mode (development)
install:
	pip install -e ".[dev]"

# First-run developer setup: install deps + pull models
dev: install
	local2 setup

# Run the test suite
test:
	PYTHONPATH=src python -m pytest tests/ -q

# Start the comparison harness (requires LoCAL2 already running on port 8000)
harness:
	python -m harness.server

# Remove build artifacts and Python caches
clean:
	rm -rf src/local/api/static dist build *.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete
