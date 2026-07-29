from agent_smith.sandbox.process import Sandbox


def show(label, r):
    print(f"\n--- {label}")
    print(f"    outcome  : {r.outcome.value}")
    if r.stdout:
        print(f"    stdout   : {r.stdout.strip()!r}")
    if r.error:
        first = r.error.strip().splitlines()[-1]
        print(f"    error    : {first}")
    if r.final_answer:
        print(f"    answer   : {r.final_answer!r}")
    print(f"    duration : {r.duration_ms:.0f}ms")


def main():
    with Sandbox(timeout=2.0) as sb:
        show("1. define a variable", sb.execute("x = 41"))
        show("2. namespace persists across calls", sb.execute("print(x + 1)"))
        show(
            "3. define a function, use it next call",
            sb.execute("def double(n):\n    return n * 2\n"),
        )
        show("4. function still there", sb.execute("print(double(x))"))
        show("5. a normal exception", sb.execute("1 / 0"))
        show("6. namespace survived the exception", sb.execute("print('x is', x)"))

        # Soft timeout: prints, then spins. SIGALRM interrupts the loop.
        show(
            "7. SOFT timeout, partial output kept",
            sb.execute(
                "print('work done before the deadline')\nwhile True:\n    pass\n"
            ),
        )
        show(
            "8. namespace survived the soft timeout",
            sb.execute("print('x is still', x)"),
        )

        # Hard timeout: adversarial code swallows the alarm exception forever.
        show(
            "9. HARD timeout, alarm swallowed by user code",
            sb.execute(
                "while True:\n"
                "    try:\n"
                "        pass\n"
                "    except TimeoutError:\n"
                "        pass\n"
            ),
        )
        print(f"\n    restarts so far: {sb.restarts}")
        show("10. namespace was LOST after respawn", sb.execute("print(x)"))

        show(
            "11. final_answer unwinds the loop",
            sb.execute("y = 7\nfinal_answer(f'the answer is {y}')"),
        )
        show(
            "12. SystemExit is forwarded, not swallowed",
            sb.execute("raise SystemExit(3)"),
        )
        show("13. sandbox still usable afterwards", sb.execute("print('alive')"))

    print("\nall done")


if __name__ == "__main__":
    main()
