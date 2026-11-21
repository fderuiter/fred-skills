.DEFAULT_GOAL := check

export PYTHONDONTWRITEBYTECODE := 1

ifneq ($(wildcard .venv/bin/python),)
PYTHON ?= .venv/bin/python
else ifneq ($(wildcard .venv/Scripts/python.exe),)
PYTHON ?= .venv/Scripts/python.exe
else
PYTHON ?= python
endif

ifneq ($(wildcard .venv/bin/ruff),)
RUFF ?= .venv/bin/ruff
else ifneq ($(wildcard .venv/Scripts/ruff.exe),)
RUFF ?= .venv/Scripts/ruff.exe
else
RUFF ?= ruff
endif

.PHONY: check fix test

check:
	$(PYTHON) -B scripts/run_checks.py

fix:
	$(RUFF) format .
	$(RUFF) check --fix .
	$(PYTHON) -B scripts/sync_audit_resources.py
	$(PYTHON) -B scripts/build_catalog.py
	$(PYTHON) -B scripts/bump_version.py
	$(PYTHON) -B scripts/build_experience.py

test:
	$(PYTHON) -B scripts/run_checks.py --phase tests
