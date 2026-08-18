from agent_smith.cli.mbpp import main

# Ctrl-C leaves by the shell's own convention rather than by a traceback. The
# cleanup has already run by the time this catches: `solve` holds its sandbox
# in a `with`, which unwinds on any `BaseException`.
try:
    main.main()
except KeyboardInterrupt:
    raise SystemExit(130) from None
