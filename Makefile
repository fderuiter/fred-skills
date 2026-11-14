.DEFAULT_GOAL := check

PYTHON ?= python

.PHONY: check fix test

check:
	$(PYTHON) scripts/sync_audit_resources.py --check
	$(PYTHON) scripts/build_catalog.py --check
	$(PYTHON) scripts/build_experience.py --check
	$(PYTHON) scripts/validate_skills.py
	$(PYTHON) -m unittest discover -s tests -v

fix:
	$(PYTHON) scripts/sync_audit_resources.py
	$(PYTHON) scripts/build_catalog.py
	$(PYTHON) scripts/build_experience.py

test:
	$(PYTHON) -m unittest discover -s tests -v
