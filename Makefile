.PHONY: all install-dev test test-all lint format clean release release-test

all: lint test

# Development setup
install-dev:
	pip install -e .[dev]
	pre-commit install

# Testing
test:
	pytest tests/ -m "not slow" -v

test-all:
	pytest tests/ -v

# Code quality
lint:
	ruff check pyznap/ tests/

format:
	ruff format pyznap/ tests/
	ruff check --fix pyznap/ tests/

format-check:
	ruff format --check pyznap/ tests/
	ruff check pyznap/ tests/

# Release
release:
	pip install twine build
	python -m build
	twine upload dist/*
	rm -rf build/ dist/ *.egg-info/

release-test:
	pip install twine build
	python -m build
	twine upload --repository-url https://test.pypi.org/legacy/ dist/*
	rm -rf build/ dist/ *.egg-info/

# Cleanup
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf pyznap/__pycache__/
	rm -rf tests/__pycache__/
	rm -rf .pytest_cache/
	rm -rf .ruff_cache/
	rm -rf .cache/
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
