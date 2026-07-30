"""Twenty-one real-shaped LLM replies, end to end.

The card's done-when. These are written the way models actually reply — with
preambles, sign-offs, stray markers and half-finished blocks — rather than as
minimal inputs for one branch each. A change that keeps the unit tests green and
breaks these has broken the thing that matters.
"""

import ast

import pytest

from agent_smith.extraction import Strategy, extract_code

CASES: list[tuple[str, str, Strategy, str]] = [
    (
        "tagged fence with a preamble",
        "Sure! Here's the function:\n\n```python\ndef add(a, b):\n    return a + b\n```",
        Strategy.FENCED,
        "def add(a, b):\n    return a + b",
    ),
    (
        "fence with a sign-off after it",
        "```python\nresult = sum([1, 2])\n```\n\nLet me know if that helps!",
        Strategy.FENCED,
        "result = sum([1, 2])",
    ),
    (
        "untagged fence",
        "```\nprint('hello')\n```",
        Strategy.FENCED,
        "print('hello')",
    ),
    (
        "py tag rather than python",
        "```py\nx = 1\n```",
        Strategy.FENCED,
        "x = 1",
    ),
    (
        "fence closed by end_code",
        "Thought: I'll compute it.\n```python\nresult = 6 * 7\n```<end_code>",
        Strategy.FENCED,
        "result = 6 * 7",
    ),
    (
        "unclosed fence, truncated generation",
        "```python\nresult = final_answer(42)",
        Strategy.FENCED,
        "result = final_answer(42)",
    ),
    (
        "two fences, the first wins",
        "```python\nfirst = 1\n```\nOr maybe:\n```python\nsecond = 2\n```",
        Strategy.FENCED,
        "first = 1",
    ),
    (
        "fence with prose stuck inside it",
        "```python\nHere is the code:\nresult = 1 + 1\n```",
        Strategy.FENCED,
        "result = 1 + 1",
    ),
    (
        "xml invoke, single string parameter",
        (
            '<invoke name="read_file">\n'
            '<parameter name="filepath">/testbed/setup.py</parameter>\n'
            "</invoke>"
        ),
        Strategy.XML,
        "result_1_1 = read_file(filepath='/testbed/setup.py')\nprint(result_1_1)",
    ),
    (
        "xml invoke with a numeric parameter",
        (
            '<invoke name="read_file"><parameter name="filepath">a.py</parameter>'
            '<parameter name="start_line">10</parameter></invoke>'
        ),
        Strategy.XML,
        "result_1_1 = read_file(filepath='a.py', start_line=10)\nprint(result_1_1)",
    ),
    (
        "xml invoke with no parameters",
        'I will submit now.\n<invoke name="get_patch"></invoke>',
        Strategy.XML,
        "result_1_1 = get_patch()\nprint(result_1_1)",
    ),
    (
        "two xml invokes",
        '<invoke name="list_files"></invoke><invoke name="get_patch"></invoke>',
        Strategy.XML,
        (
            "result_1_1 = list_files()\nprint(result_1_1)\n"
            "result_1_2 = get_patch()\nprint(result_1_2)"
        ),
    ),
    (
        "hermes tool call",
        (
            "<tool_call>\n"
            '{"name": "search_code", "arguments": {"pattern": "def solve"}}\n'
            "</tool_call>"
        ),
        Strategy.HERMES,
        "result_1_1 = search_code(pattern='def solve')\nprint(result_1_1)",
    ),
    (
        "hermes with a null argument",
        (
            '<tool_call>{"name": "read_file", '
            '"arguments": {"filepath": "a.py", "end_line": null}}</tool_call>'
        ),
        Strategy.HERMES,
        "result_1_1 = read_file(filepath='a.py', end_line=None)\nprint(result_1_1)",
    ),
    (
        "hermes with an apostrophe in a value",
        '<tool_call>{"name": "note", "arguments": {"text": "O\'Brien"}}</tool_call>',
        Strategy.HERMES,
        'result_1_1 = note(text="O\'Brien")\nprint(result_1_1)',
    ),
    (
        "hermes with a trailing comma, repaired",
        '<tool_call>{"name": "get_patch",}</tool_call>',
        Strategy.HERMES,
        "result_1_1 = get_patch()\nprint(result_1_1)",
    ),
    (
        "two hermes calls in one reply",
        (
            '<tool_call>{"name": "run_tests"}</tool_call>\n'
            '<tool_call>{"name": "get_patch"}</tool_call>'
        ),
        Strategy.HERMES,
        (
            "result_1_1 = run_tests()\nprint(result_1_1)\n"
            "result_1_2 = get_patch()\nprint(result_1_2)"
        ),
    ),
    (
        "react pair with a thought",
        (
            "Thought: I need to see the file.\n"
            "Action: read_file\n"
            'Action Input: {"filepath": "a.py"}'
        ),
        Strategy.REACT,
        "result_1_1 = read_file(filepath='a.py')\nprint(result_1_1)",
    ),
    (
        "react with empty input",
        "Action: get_patch\nAction Input: {}",
        Strategy.REACT,
        "result_1_1 = get_patch()\nprint(result_1_1)",
    ),
    (
        "bare python, no fence at all",
        "def is_even(n):\n    return n % 2 == 0",
        Strategy.BARE,
        "def is_even(n):\n    return n % 2 == 0",
    ),
    (
        "bare python behind a preamble",
        "Here you go:\nresult = final_answer([1, 2, 3])",
        Strategy.BARE,
        "result = final_answer([1, 2, 3])",
    ),
]


@pytest.mark.parametrize(
    ("text", "strategy", "expected"),
    [(text, strategy, expected) for _, text, strategy, expected in CASES],
    ids=[name for name, _, _, _ in CASES],
)
def test_the_corpus_extracts(text: str, strategy: Strategy, expected: str) -> None:
    result = extract_code(text, step=1)

    assert result.failure is None
    assert result.strategy is strategy
    assert result.code == expected


@pytest.mark.parametrize(
    "text",
    [text for _, text, _, _ in CASES],
    ids=[name for name, _, _, _ in CASES],
)
def test_every_corpus_extraction_is_valid_python(text: str) -> None:
    code = extract_code(text, step=1).code

    assert code is not None
    ast.parse(code)
