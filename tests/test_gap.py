from __future__ import annotations

from agent_smith.sandbox.process import Sandbox
from agent_smith.sandbox.protocol import Outcome

# Every dunder is built at runtime, so the name-based AST scan has nothing to
# match. The runtime _guarded_getattr is what must close this gap: resolving
# '__base__' during the getattr chain raises AttributeBlocked.
PAYLOAD = """
c = '__cl' + 'ass__'
b = '__ba' + 'se__'
s = '__subcl' + 'asses__'
obj = getattr(getattr((), c), b)
subs = getattr(obj, s)()
print('reached subclasses, count =', len(subs))
names = [k.__name__ for k in subs]
print('found BuiltinImporter:', 'BuiltinImporter' in names)
"""


def test_computed_dunder_getattr_chain_is_blocked_at_runtime() -> None:
    with Sandbox(timeout=5.0) as sb:
        r = sb.execute(PAYLOAD)
        assert r.outcome is Outcome.BLOCKED
        # The payload never got far enough to enumerate subclasses.
        assert "reached subclasses" not in r.stdout
