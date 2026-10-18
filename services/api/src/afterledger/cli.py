"""Command line: seeding the demo families, auditing the knowledge, exporting the API contract, evaluating.

    afterledger seed [KEY ...] [--snapshot]   play the demo families' timelines into the case store
    afterledger build-demos --out DIR         seed them offline as bundles a deployment imports
    afterledger audit-knowledge               check every assertion, quote, plan and form
    afterledger export-openapi [PATH]         write the OpenAPI document the web client is generated from
    afterledger eval [--json PATH]            run the offline evaluation and print the report
    afterledger calibrate [--write]           refit the certainty tables from cases a live model prepared
    afterledger challenge COUNTRY QUESTION    run a live challenge in the terminal
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


def _seed(args: argparse.Namespace) -> int:
    from .runtime import build_deps
    from .seed import snapshot
    from .seed.families import family_keys
    from .seed.seeder import seed_family

    async def go() -> int:
        deps = build_deps()
        problems = 0
        for key in args.keys or family_keys():
            rep = await seed_family(deps, key, story_day=deps.settings.story_day)
            if args.snapshot:
                snapshot.save(deps, key)
            print(f"{key:10s} {rep.events:3d} events  {rep.seconds:6.1f} s  {rep.model_calls:4d} model calls"
                  + (f"  WARNINGS: {rep.warnings}" if rep.warnings else ""))
            problems += len(rep.warnings)
        return 1 if problems else 0

    return asyncio.run(go())


def _build_demos(args: argparse.Namespace) -> int:
    """Seeds the demo families offline (scripted responses, the local store, a scratch state directory) and
    writes each as a bundle, with the offline evaluation of those cases. A deployment imports the bundles
    (AL_DEMO_BUNDLES) instead of replaying six stories through a live model."""
    import tempfile
    from datetime import UTC, datetime

    from . import __version__
    from .config import get_settings
    from .eval.runner import run_all, write_report
    from .runtime import build_deps
    from .seed import snapshot
    from .seed.families import family_keys
    from .seed.seeder import seed_family

    out = Path(args.out).resolve()
    keys = args.keys or family_keys()

    async def go() -> int:
        with tempfile.TemporaryDirectory(prefix="afterledger-demos-") as tmp:
            s = get_settings().model_copy(update={"mode": "offline", "store": "local", "llm": "scripted",
                                                  "state_dir": Path(tmp), "demo_bundles": None,
                                                  "seed_on_start": False})
            deps = build_deps(s)
            out.mkdir(parents=True, exist_ok=True)
            problems = 0
            for key in keys:
                rep = await seed_family(deps, key, story_day=s.story_day)
                if not snapshot.save(deps, key, target=out / key):
                    print(f"{key:10s} NOT SAVED: the case was not in the store")
                    problems += 1
                    continue
                print(f"{key:10s} {rep.events:3d} events  {rep.seconds:6.1f} s"
                      + (f"  WARNINGS: {rep.warnings}" if rep.warnings else ""))
                problems += len(rep.warnings)
            if not args.no_eval:
                report = await run_all(deps, seed=False)
                write_report(report, out / "eval_latest.json")
                failed = [c["name"] for c in report.get("checks", []) if not c.get("ok")]
                print("evaluation: " + ("every check passed" if not failed else f"FAILED: {', '.join(failed)}"))
                problems += len(failed)
            (out / "manifest.json").write_text(json.dumps({
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"), "version": __version__,
                "families": keys, "llm": "scripted", "problems": problems}, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {len(keys)} demo bundles to {out}")
        return 1 if problems else 0

    return asyncio.run(go())


def _audit(args: argparse.Namespace) -> int:
    from .eval.knowledge_audit import main as audit_main
    return audit_main()


def _openapi(args: argparse.Namespace) -> int:
    from .app.main import create_app
    from .config import REPO_ROOT
    out = Path(args.path) if args.path else REPO_ROOT / "packages" / "contracts" / "openapi.json"
    doc = create_app(prepare_demo=False).openapi()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(doc.get('paths', {}))} paths, {len(doc.get('components', {}).get('schemas', {}))} schemas)")
    return 0


def _eval(args: argparse.Namespace) -> int:
    from .eval.runner import print_report, run_all, write_report
    from .runtime import build_deps

    deps = build_deps()
    report = asyncio.run(run_all(deps, seed=not args.no_seed))
    write_report(report, deps.settings.state_dir / "eval" / "latest.json")   # what the Evaluation screen shows
    if args.json:
        write_report(report, Path(args.json))
    print_report(report)
    return 0 if all(c.get("ok") for c in report.get("checks", [])) else 1


def _calibrate(args: argparse.Namespace) -> int:
    from .eval.calibrate import calibrate, print_report
    from .runtime import build_deps

    report = asyncio.run(calibrate(build_deps(), write=args.write))
    print_report(report)
    return 0


def _challenge(args: argparse.Namespace) -> int:
    from .modules.challenge import start_challenge
    from .runtime import build_deps

    async def go() -> int:
        deps = build_deps()

        def show(ev: dict) -> None:
            print(f"{ev['at']:7.2f}s  {ev['stage']:15s} {ev['text']}")

        run = await start_challenge(deps, country=args.country, question=args.question, lang=args.lang,
                                    holding_kind=args.kind, on_event=show)
        if run.letter:
            print("\n" + (run.letter.get("body") or ""))
        return 0 if run.status == "done" else 1

    return asyncio.run(go())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="afterledger", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("seed", help="seed the demo families")
    s.add_argument("keys", nargs="*")
    s.add_argument("--snapshot", action="store_true", help="also save a snapshot for fast resets")
    s.set_defaults(fn=_seed)
    b = sub.add_parser("build-demos", help="seed the demo families offline and write them as bundles")
    b.add_argument("keys", nargs="*")
    b.add_argument("--out", required=True, help="folder to write the bundles to (AL_DEMO_BUNDLES)")
    b.add_argument("--no-eval", action="store_true", help="skip the offline evaluation of the bundles")
    b.set_defaults(fn=_build_demos)
    a = sub.add_parser("audit-knowledge", help="check every assertion, quote, plan and form")
    a.set_defaults(fn=_audit)
    o = sub.add_parser("export-openapi", help="write the OpenAPI document")
    o.add_argument("path", nargs="?")
    o.set_defaults(fn=_openapi)
    e = sub.add_parser("eval", help="run the offline evaluation")
    e.add_argument("--json")
    e.add_argument("--no-seed", action="store_true", help="evaluate the cases already in the store")
    e.set_defaults(fn=_eval)
    k = sub.add_parser("calibrate", help="refit the certainty tables from cases a live model prepared")
    k.add_argument("--write", action="store_true", help="write the refitted tables to data/knowledge/calibration.json")
    k.set_defaults(fn=_calibrate)
    c = sub.add_parser("challenge", help="run a live challenge in the terminal")
    c.add_argument("country")
    c.add_argument("question")
    c.add_argument("--lang")
    c.add_argument("--kind", default=None, help="holding kind, e.g. savings or life_policy")
    c.set_defaults(fn=_challenge)
    args = p.parse_args(argv)
    return int(args.fn(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
