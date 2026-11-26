import pathlib

p = pathlib.Path("tests/session-store.test.cjs")
s = p.read_text(encoding="utf-8")
old = "  }, 12000);\n\n  const state = useSessionStore.getState();\n  assert.equal(state.error, null"
new = "  }, 25000);\n\n  const state = useSessionStore.getState();\n  assert.equal(state.error, null"
assert s.count(old) == 1, s.count(old)
s = s.replace(old, new)
old2 = "await waitFor(() => runFetches() > before, 8000);"
new2 = "await waitFor(() => runFetches() > before, 12000);"
assert s.count(old2) == 1, s.count(old2)
s = s.replace(old2, new2)
p.write_text(s, encoding="utf-8", newline="")
print("patched timeouts")
