.PHONY: install test lint run web

install:
	python3 -m venv .venv
	.venv/bin/python -m pip install --upgrade pip
	.venv/bin/python -m pip install -e '.[dev]'

test:
	.venv/bin/python -m pytest

lint:
	.venv/bin/python -m ruff check src scripts tests
	.venv/bin/python -m mypy src scripts

run:
	.venv/bin/wimb

web:
	.venv/bin/uvicorn wimb.web.app:app --reload --host 127.0.0.1 --port 8000
