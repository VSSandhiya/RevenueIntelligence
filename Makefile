PYTHON ?= python

.PHONY: run demo reset test export replay

run:
	$(PYTHON) -m uvicorn inbox.api:app --host 127.0.0.1 --port 8000 --reload

demo:
	$(PYTHON) -m inbox.cli demo

reset:
	$(PYTHON) -m inbox.cli reset

test:
	$(PYTHON) -m pytest -q

export:
	$(PYTHON) -m inbox.cli export

replay:
	$(PYTHON) -m inbox.replay