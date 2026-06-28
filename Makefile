PYTHON ?= .venv/bin/python
PIP ?= .venv/bin/pip

.PHONY: setup init-db run-bump run-sinc run-cross eval-smoke clinical-repeat

setup:
	python3.8 -m venv .venv
	$(PIP) install -r requirements.txt

init-db:
	$(PYTHON) scripts/db/init_shared_db.py --dataset-scope cross-dataset

run-bump:
	$(PYTHON) query_system.py --shared-db --dataset-scope bump

run-sinc:
	$(PYTHON) query_system.py --shared-db --dataset-scope sinc

run-cross:
	$(PYTHON) query_system.py --shared-db --dataset-scope cross-dataset

eval-smoke:
	$(PYTHON) scripts/eval/test_simulated_prompts.py --dataset-scope bump --shared-db --max-cases 5

clinical-repeat:
	$(PYTHON) clinical_notes_analysis/scripts/run_repeated_adverse_event_column_tests.py --repeats 3
