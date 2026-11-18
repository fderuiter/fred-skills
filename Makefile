.DEFAULT_GOAL := check

PYTHON ?= python
RUFF ?= ruff

.PHONY: check fix test

check:
	$(PYTHON) scripts/run_checks.py

fix:
	$(RUFF) format .
	$(RUFF) check --fix .
	$(PYTHON) scripts/sync_audit_resources.py
	$(PYTHON) scripts/build_catalog.py
	$(PYTHON) scripts/build_experience.py

test:
	$(PYTHON) -m unittest discover -s tests -v
