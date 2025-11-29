.PHONY: all install-dev test test-unit test-root test-integration test-ssh test-setup lint format clean release release-test

# Privilege escalation: pkexec (GUI) or sudo
SUDO := $(shell command -v pkexec 2>/dev/null || command -v sudo 2>/dev/null)

all: lint test

# Development setup
install-dev:
	pip install -e .[dev]
	pre-commit install

# Setup test environment (packages, SSH)
test-setup:
	./scripts/test-setup.sh

# ALL tests (unit + root)
test: test-unit test-root

# Unit tests (no root required)
test-unit:
	pytest tests/unit/ -v

# All tests requiring root
test-root: test-integration test-ssh

# Integration tests (root + ZFS)
# Note: pkexec changes cwd to /root, so we use absolute paths
test-integration:
	$(SUDO) pytest $(CURDIR)/tests/test_functions.py $(CURDIR)/tests/test_pyznap.py -v

# SSH tests (root + ZFS + SSH)
test-ssh:
	$(SUDO) pytest $(CURDIR)/tests/test_functions_ssh.py $(CURDIR)/tests/test_pyznap_ssh.py -v

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
