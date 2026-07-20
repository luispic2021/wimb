.PHONY: install test lint run

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
