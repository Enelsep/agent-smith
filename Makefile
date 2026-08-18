VENV_DIR = .venv
SRC_DIR = src
FIXTURES_DIR = tests/fixtures

.PHONY: install run clean lint test check dev-mbpp dev-swe

$(VENV_DIR)/bin/activate:
	@echo "Creating virtual environment..."
	uv venv $(VENV_DIR)

install: $(VENV_DIR)/bin/activate
	@echo "Installing dependencies..."
	uv sync

run: install
	@echo "Running Agent Smith..."
	uv run python3 main.py

dev-mbpp: install
	@echo "Running MBPP development harness..."
	uv run python -m agent_mbpp --task-file $(FIXTURES_DIR)/mbpp_tasks.json --output solution.json --model-name qwen/qwen3-235b-a22b-2507 --provider-url https://openrouter.ai/api/v1

dev-swe: install
	@echo "Running SWE-bench development harness..."
	uv run python -m agent_swebench --task-file $(FIXTURES_DIR)/swebench_tasks.json --output solution.json --model-name qwen/qwen3-235b-a22b-2507 --provider-url https://openrouter.ai/api/v1

clean:
	@echo "Cleaning up temporary files and caches..."
	rm -rf $(VENV_DIR)
	find . -type d -name "__pycache__" -exec rm -rf {} +
	find . -type d -name ".mypy_cache" -exec rm -rf {} +
	find . -type d -name ".pytest_cache" -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete


lint: install
	@echo "Linting and type-checking..."
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy .

