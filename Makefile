.PHONY: validate lint format test install

# Install dependencies
install:
	pip install -e ".[dev]"

# Run validation
validate:
	python3 scripts/validate.py

# Run linting
lint:
	python3 -m flake8 jupytercluster/ --count --select=E9,F63,F7,F82 --show-source --statistics
	python3 -m black --check jupytercluster/ tests/
	python3 -m isort --check-only jupytercluster/ tests/

# Auto-fix formatting
format:
	python3 -m black jupytercluster/ tests/
	python3 -m isort jupytercluster/ tests/

# Run tests
test:
	pytest tests/ -v

# Run all checks (validate + test)
check: validate test
