VENV_DIR = .venv
SRC_DIR = src

.PHONY: install run clean lint test check

source $(VENV_DIR)/bin/activate:
	@echo "Creating virtual environment..."
	uv venv $(VENV_DIR)

install: $(VENV_DIR)/bin/activate
	@echo "Installing dependencies..."
	uv sync

run: install
	@echo "Running Agent Smith..."
	uv run python3 main.py

clean:
	@echo "Cleaning up temporary files and caches..."
	rm -rf $(VENV_DIR)
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# ruff replaces flake8 and its plugins; mypy is kept because ruff does no type
# inference across modules. The two do not overlap.
lint: install
	@echo "Linting and type-checking..."
	uv run ruff check .
	uv run mypy .

test: install
	@echo "Running tests..."
	uv run pytest -q

# Run this before every push: it is the gate CONTRIBUTING.md refers to.
check: lint test
