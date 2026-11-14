.DEFAULT_GOAL := check

PYTHON ?= python
RUFF ?= ruff

.PHONY: check fix test

check:
	$(RUFF) check .
	$(RUFF) format --check .
	$(PYTHON) scripts/sync_audit_resources.py --check
	$(PYTHON) scripts/build_catalog.py --check
	$(PYTHON) scripts/build_experience.py --check
	$(PYTHON) scripts/validate_skills.py
	$(PYTHON) scripts/check_doc_links.py
	$(PYTHON) scripts/verify_doc_snippets.py
	$(PYTHON) -m unittest discover -s tests -v

fix:
	$(RUFF) format .
	$(RUFF) check --fix .
	$(PYTHON) scripts/sync_audit_resources.py
	$(PYTHON) scripts/build_catalog.py
	$(PYTHON) scripts/build_experience.py

test:
	$(PYTHON) -m unittest discover -s tests -v
