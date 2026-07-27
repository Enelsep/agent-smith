VENV_DIR = .venv
SRC_DIR = src

.PHONY: install run clean lint

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

lint: install
	@echo "Running standard linting..."
	uv run flake8 src
	uv run mypy src
