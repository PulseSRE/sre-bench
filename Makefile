.PHONY: ci verify test lint

# Reproduce CI exactly, in a throwaway venv with the SAME extras CI installs.
#
# This target exists because "it passed locally" once meant something different
# from "it passes in CI": a dev venv with the optional [judge] extra installed
# hid a hard dependency on `anthropic` that the base install does not have.
# Running the suite in your own venv cannot catch that class of bug; running it
# in a clean .[dev] install can.
CI_VENV := .ci-venv
# CI runs Python 3.11/3.12. A macOS system python3 is often older than this
# project's floor, so prefer uv (which can provision an interpreter) and fall
# back to an explicitly versioned python3 if uv is not installed.
UV := $(shell command -v uv 2>/dev/null)
CI_PYTHON ?= $(shell command -v python3.12 || command -v python3.11)

ci:
	@rm -rf $(CI_VENV)
ifneq ($(UV),)
	@uv venv --python 3.12 $(CI_VENV) >/dev/null
	@VIRTUAL_ENV=$(CI_VENV) uv pip install -q -e ".[dev]"
else
	@test -n "$(CI_PYTHON)" || (echo "Need python3.11+ or uv installed" && exit 1)
	@$(CI_PYTHON) -m venv $(CI_VENV)
	@$(CI_VENV)/bin/pip install -q --upgrade pip
	@$(CI_VENV)/bin/pip install -q -e ".[dev]"
endif
	@echo "--- ruff (CI parity) ---"
	@$(CI_VENV)/bin/ruff check .
	@echo "--- pytest (CI parity: no optional extras) ---"
	@$(CI_VENV)/bin/pytest tests/ -q
	@echo "--- reference gate (CI parity) ---"
	@$(CI_VENV)/bin/sre-bench reference-scores
	@echo "CI parity check passed."

# Fast local loop — uses your existing .venv, extras and all.
verify: lint test

lint:
	@ruff check .

test:
	@pytest tests/ -q
