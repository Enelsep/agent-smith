"""Signpost. The work happens behind the three entry points listed below."""

ENTRY_POINTS = (
    "uv run python -m agent_mbpp --task-file <task.json> --output <solution.json>",
    "uv run python -m agent_swebench --task-file <task.json> --output <solution.json>",
    "uv run sandbox [config.json] [--mcp-stdio COMMAND | --mcp-server URL]",
)


def main() -> None:
    print("Agent Smith. Entry points:")
    for command in ENTRY_POINTS:
        print(f"  {command}")


if __name__ == "__main__":
    main()
