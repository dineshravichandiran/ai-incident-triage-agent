.PHONY: install test demo baseline
install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
test:
	.venv/bin/python -m pytest -q
demo:
	.venv/bin/python -m triage demo E01 --yes
baseline:
	.venv/bin/python -m evals.run --backend rules --set eval
