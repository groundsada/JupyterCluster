.PHONY: help install test lint format clean build docker-build docker-push

help:
	@echo "Available targets:"
	@echo "  install      - Install dependencies"
	@echo "  test         - Run tests"
	@echo "  lint         - Run linters"
	@echo "  format       - Format code"
	@echo "  clean        - Clean build artifacts"
	@echo "  build        - Build Python package"
	@echo "  docker-build - Build Docker image"
	@echo "  docker-push  - Push Docker image"

install:
	pip install -e ".[dev]"

test:
	pytest tests/ -v --cov=jupytercluster --cov-report=term-missing

test-unit:
	pytest tests/ -v -m unit

test-integration:
	pytest tests/ -v -m integration

lint:
	flake8 jupytercluster/ --max-line-length=100
	black --check jupytercluster/
	isort --check-only jupytercluster/
	mypy jupytercluster/ --ignore-missing-imports || true

format:
	black jupytercluster/
	isort jupytercluster/

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache .coverage htmlcov/

build:
	python -m build

docker-build:
	docker build -t jupytercluster:latest .

docker-push:
	docker push jupytercluster:latest

helm-lint:
	helm lint helm/jupytercluster/

helm-template:
	helm template jupytercluster helm/jupytercluster/

