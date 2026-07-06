"""
_import_check.py — cross-session guardrail.

Imports every module in scripts/ and reports any that fail. A broken import
means a path, signature, or dependency drifted between sessions (e.g. one chat
renamed a function another chat still calls). Wired as a Stop hook in
.claude/settings.json so it runs at the end of every session.

Standalone (no deps beyond stdlib). Exit 0 = all clean, 1 = something broke.
Run manually any time:  cd scripts && python _import_check.py

With --hook: stays exit 0 (never blocks the session) and, on failure, prints a
JSON {"systemMessage": ...} so Claude Code surfaces the break in the UI. Reads
and ignores hook JSON on stdin.
"""
import importlib, pathlib, sys, io, contextlib, json

HOOK = "--hook" in sys.argv
if HOOK:
    try:
        sys.stdin.read()   # drain hook payload; we don't need it
    except Exception:
        pass

sys.path.insert(0, str(pathlib.Path(__file__).parent))

SKIP = {"_import_check"}  # don't import self

mods = sorted(p.stem for p in pathlib.Path(__file__).parent.glob("*.py")
              if p.stem not in SKIP)

bad = []
for m in mods:
    try:
        # swallow import-time stdout/stderr so a chatty module doesn't spam the hook
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            importlib.import_module(m)
    except Exception as e:
        bad.append(f"{m}: {type(e).__name__}: {e}")

if bad:
    detail = "⚠ scripts/ import-check FAILED — a cross-session break:\n" + "\n".join("  " + b for b in bad)
    if HOOK:
        # never block the session; just surface the message in the UI
        print(json.dumps({"systemMessage": detail}))
        sys.exit(0)
    print(detail)
    sys.exit(1)

if not HOOK:
    print(f"import-check OK ({len(mods)} modules)")
sys.exit(0)
