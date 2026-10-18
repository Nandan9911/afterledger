#!/usr/bin/env python3
"""One entry point for working on AfterLedger, the same on Windows, macOS and Linux.

    python tools/dev.py setup            install the API (uv) and the web app (pnpm), create .env
    python tools/dev.py demos            build the six demo cases once (offline), so the API starts instantly
    python tools/dev.py dev              run the API (port 8080) and the web app (port 3000) together, for editing
    python tools/dev.py start            the same, as in production: the web app built once and served fast,
                                         without development tools (add --no-build to reuse the last build)
    python tools/dev.py api [--port N]   run the API alone            (add --reload while editing)
    python tools/dev.py web [--port N]   run the web app alone
    python tools/dev.py test             Python tests (fast set) and the web unit tests
    python tools/dev.py test --all       also the slow end-to-end runs over every demo family
    python tools/dev.py check            everything CI checks: lint, types, tests, messages, contract drift
    python tools/dev.py contracts        regenerate the OpenAPI document and the web client's types
    python tools/dev.py eval             run the offline evaluation and print the report
    python tools/dev.py e2e              browser tests (Playwright and axe); uses the API and web app if they are
                                         running, otherwise starts both offline

Live runs: set AL_MODE=live, AL_LLM=gemini and your Google credentials in .env (see docs/runbooks/live-run.md).
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = ROOT / "services" / "api"
WEB = ROOT / "apps" / "web"
WINDOWS = os.name == "nt"
BUNDLES = ROOT / ".data" / "demo_bundles"


def say(text: str) -> None:
    print(f"\n== {text}", flush=True)


def dotenv() -> dict[str, str]:
    """The KEY=VALUE lines of the repository's .env. The API reads the file itself; the web app runs in apps/web,
    so the settings it needs are passed on from here."""
    f = ROOT / ".env"
    out: dict[str, str] = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def env() -> dict[str, str]:
    e = dict(os.environ)
    e.setdefault("PYTHONUTF8", "1")               # Windows consoles: read and write UTF-8 everywhere
    e.setdefault("PYTHONIOENCODING", "utf-8")
    if BUNDLES.exists() and "AL_DEMO_BUNDLES" not in e:
        e["AL_DEMO_BUNDLES"] = str(BUNDLES)      # demo cases restore from the bundles in a fraction of a second
    for name in ("AL_API_BASE", "AL_SITE", "AL_AUTH"):     # the web app reads these when it serves a page
        if name not in e and dotenv().get(name):
            e[name] = dotenv()[name]
    return e


def tool(name: str) -> str:
    found = shutil.which(name)                    # finds pnpm.cmd and uv.exe on Windows too
    if not found:
        sys.exit(f"'{name}' was not found on PATH. See the README for what to install.")
    return found


def py() -> str:
    exe = API / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")
    if not exe.exists():
        sys.exit("The API's virtual environment is missing. Run: python tools/dev.py setup")
    return str(exe)


def run(cmd: list[str], cwd: Path = ROOT, check: bool = True) -> int:
    print("$ " + " ".join(cmd), flush=True)
    code = subprocess.call(cmd, cwd=cwd, env=env())
    if check and code != 0:
        sys.exit(code)
    return code


def setup(_: argparse.Namespace) -> None:
    say("API: Python 3.12 environment with uv")
    run([tool("uv"), "python", "install", "3.12"], cwd=API, check=False)
    run([tool("uv"), "sync", "--python", "3.12", "--all-extras", "--group", "dev"], cwd=API)
    say("Web app and contracts: pnpm install")
    run([tool("pnpm"), "install"], cwd=ROOT)
    if not (ROOT / ".env").exists():
        shutil.copyfile(ROOT / ".env.example", ROOT / ".env")
        print("Created .env from .env.example (offline mode).")
    print("\nReady. Next: python tools/dev.py demos, then python tools/dev.py dev")


def demos(args: argparse.Namespace) -> None:
    say("Building the six demo cases offline (about three minutes, once)")
    run([py(), "-m", "afterledger.cli", "build-demos", "--out", str(args.out)], cwd=API)


def api_cmd(port: int, reload: bool) -> list[str]:
    cmd = [py(), "-m", "uvicorn", "afterledger.app.main:app", "--host", "0.0.0.0", "--port", str(port)]
    if reload:
        cmd += ["--reload", "--reload-dir", str(API / "src")]
    return cmd


def web_cmd(port: int, production: bool = False) -> list[str]:
    return [tool("pnpm"), "--filter", "@afterledger/web", "exec", "next", "start" if production else "dev",
            "--port", str(port)]


def api(args: argparse.Namespace) -> None:
    run(api_cmd(args.port, args.reload), cwd=API)


def web(args: argparse.Namespace) -> None:
    run(web_cmd(args.port), cwd=ROOT)


def start(args: argparse.Namespace) -> None:
    """As in production: the web app is built once and served by `next start` (pages open at once, no
    development indicator), and the API runs without the reloader."""
    if not args.no_build:
        say("Building the web app for production (a minute or two)")
        run([tool("pnpm"), "--filter", "@afterledger/web", "build"], cwd=ROOT)
    dev(argparse.Namespace(reload=False, production=True))


def dev(args: argparse.Namespace) -> None:
    """Both servers; Ctrl+C stops both. On Windows each runs in its own process group, so one Ctrl+Break
    reaches the server and the processes it started (uvicorn's reloader, Next.js's workers)."""
    group = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {}
    procs = [subprocess.Popen(api_cmd(8080, args.reload), cwd=API, env=env(), **group),
             subprocess.Popen(web_cmd(3000, getattr(args, "production", False)), cwd=ROOT, env=env(), **group)]
    print("\nAPI on http://localhost:8080 , web app on http://localhost:3000 (Ctrl+C stops both)\n", flush=True)
    try:
        while all(p.poll() is None for p in procs):
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.CTRL_BREAK_EVENT if WINDOWS else signal.SIGINT)
        for p in procs:
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()


def test(args: argparse.Namespace) -> None:
    say("Python tests")
    run([py(), "-m", "pytest", "-q"] + ([] if args.all else ["-m", "not slow"]), cwd=API)
    say("Web unit tests")
    run([tool("pnpm"), "--filter", "@afterledger/web", "test"], cwd=ROOT)


def contracts(_: argparse.Namespace) -> None:
    run([py(), "-m", "afterledger.cli", "export-openapi"], cwd=API)
    run([tool("pnpm"), "contracts"], cwd=ROOT)


def check(args: argparse.Namespace) -> None:
    say("Python lint")
    run([py(), "-m", "ruff", "check", "src", "tests"], cwd=API)
    say("API contract is up to date")
    with tempfile.TemporaryDirectory() as tmp:
        fresh = Path(tmp) / "openapi.json"
        run([py(), "-m", "afterledger.cli", "export-openapi", str(fresh)], cwd=API)
        current = ROOT / "packages" / "contracts" / "openapi.json"
        if fresh.read_text(encoding="utf-8") != current.read_text(encoding="utf-8"):
            sys.exit("packages/contracts/openapi.json is out of date: run python tools/dev.py contracts")
    say("Knowledge audit (every quote on its page, every plan and form)")
    run([py(), "-m", "afterledger.cli", "audit-knowledge"], cwd=API)
    say("Web: messages in every language, types, lint")
    run([tool("pnpm"), "--filter", "@afterledger/web", "check:messages"], cwd=ROOT)
    run([tool("pnpm"), "--filter", "@afterledger/web", "typecheck"], cwd=ROOT)
    run([tool("pnpm"), "--filter", "@afterledger/web", "lint"], cwd=ROOT)
    test(args)
    print("\nEverything passed.")


def evaluate(_: argparse.Namespace) -> None:
    run([py(), "-m", "afterledger.cli", "eval"], cwd=API)


def e2e(args: argparse.Namespace) -> None:
    if not BUNDLES.exists():
        demos(argparse.Namespace(out=BUNDLES))
    say("Browser tests (Playwright and axe)")
    run([tool("pnpm"), "--filter", "@afterledger/web", "exec", "playwright", "install", "chromium"], cwd=ROOT)
    run([tool("pnpm"), "--filter", "@afterledger/web", "e2e"], cwd=ROOT)


def main() -> None:
    p = argparse.ArgumentParser(prog="dev.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup").set_defaults(fn=setup)
    d = sub.add_parser("demos")
    d.add_argument("--out", type=Path, default=BUNDLES)
    d.set_defaults(fn=demos)
    for name, fn in (("api", api), ("web", web)):
        s = sub.add_parser(name)
        s.add_argument("--port", type=int, default=8080 if name == "api" else 3000)
        if name == "api":
            s.add_argument("--reload", action="store_true")
        s.set_defaults(fn=fn)
    s = sub.add_parser("dev")
    s.add_argument("--reload", action="store_true")
    s.set_defaults(fn=dev)
    s = sub.add_parser("start")
    s.add_argument("--no-build", action="store_true", help="serve the last production build")
    s.set_defaults(fn=start)
    for name, fn in (("test", test), ("check", check)):
        s = sub.add_parser(name)
        s.add_argument("--all", action="store_true", help="include the slow end-to-end runs")
        s.set_defaults(fn=fn)
    sub.add_parser("contracts").set_defaults(fn=contracts)
    sub.add_parser("eval").set_defaults(fn=evaluate)
    sub.add_parser("e2e").set_defaults(fn=e2e)
    args = p.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
