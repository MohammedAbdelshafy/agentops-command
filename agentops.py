#!/usr/bin/env python3
"""AgentOps Command - ops CLI for agent runs.

Tracks agent runs on a local JSON store (default ./.agentops/store.json,
overridable via --store or the AGENTOPS_STORE env var):

    agentops register <run-id> [--agent NAME] [--meta k=v ...]
    agentops log <run-id> <message> [--level info|warn|error]
    agentops status <run-id> [--state running|succeeded|failed]
    agentops cost <run-id> --tokens-in N --tokens-out M [--cost-usd X]
    agentops report [--format md|json] [--out FILE]
    agentops list

Standard library only. All writes are atomic (temp file + os.replace).
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone

VERSION = "0.1.0"
VALID_STATES = ("running", "succeeded", "failed")
VALID_LEVELS = ("info", "warn", "error")


def default_store_path():
    return os.environ.get("AGENTOPS_STORE") or os.path.join(".", ".agentops", "store.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_store(path):
    """Load the store; return {} for a missing file."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"store at {path} is corrupt: expected a JSON object")
    return data


def save_store(path, data):
    """Atomically write the store: temp file in the same dir + rename."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".store", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, os.path.abspath(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def require_run(store, run_id):
    try:
        return store[run_id]
    except KeyError:
        raise ValueError(f"unknown run id: {run_id}")


def parse_meta(pairs):
    meta = {}
    for pair in pairs or []:
        if "=" not in pair:
            raise ValueError(f"meta must be k=v, got: {pair!r}")
        key, value = pair.split("=", 1)
        if not key:
            raise ValueError(f"meta key must be non-empty, got: {pair!r}")
        meta[key] = value
    return meta


def cmd_register(args):
    store = load_store(args.store)
    if args.run_id in store:
        raise ValueError(f"run already exists: {args.run_id}")
    store[args.run_id] = {
        "run_id": args.run_id,
        "agent": args.agent,
        "meta": parse_meta(args.meta),
        "state": "running",
        "started_at": now_iso(),
        "ended_at": None,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "logs": [],
    }
    save_store(args.store, store)
    print(f"registered run {args.run_id}")
    return 0


def cmd_log(args):
    store = load_store(args.store)
    run = require_run(store, args.run_id)
    run["logs"].append(
        {"ts": now_iso(), "level": args.level, "message": args.message}
    )
    save_store(args.store, store)
    print(f"logged to run {args.run_id}")
    return 0


def cmd_status(args):
    store = load_store(args.store)
    run = require_run(store, args.run_id)
    if args.state is not None:
        run["state"] = args.state
        if args.state in ("succeeded", "failed") and run["ended_at"] is None:
            run["ended_at"] = now_iso()
        elif args.state == "running":
            run["ended_at"] = None
        save_store(args.store, store)
    run = require_run(store, args.run_id)
    print(f"run:      {run['run_id']}")
    print(f"agent:    {run['agent'] or '-'}")
    print(f"state:    {run['state']}")
    print(f"started:  {run['started_at']}")
    print(f"ended:    {run['ended_at'] or '-'}")
    print(f"tokens:   in={run['tokens_in']} out={run['tokens_out']}")
    print(f"cost_usd: {run['cost_usd']}")
    print(f"logs:     {len(run['logs'])} entries")
    if run["meta"]:
        for key, value in run["meta"].items():
            print(f"meta:     {key}={value}")
    return 0


def cmd_cost(args):
    store = load_store(args.store)
    run = require_run(store, args.run_id)
    run["tokens_in"] = args.tokens_in
    run["tokens_out"] = args.tokens_out
    if args.cost_usd is not None:
        run["cost_usd"] = args.cost_usd
    save_store(args.store, store)
    print(f"recorded cost for run {args.run_id}")
    return 0


def last_error_line(run):
    for entry in reversed(run["logs"]):
        if entry.get("level") == "error":
            return entry.get("message", "")
    if run["logs"]:
        return run["logs"][-1].get("message", "")
    return "(no log entries)"


def build_report(store):
    runs = list(store.values())
    by_state = {state: 0 for state in VALID_STATES}
    tokens_in = 0
    tokens_out = 0
    cost_usd = 0.0
    failures = []
    for run in runs:
        state = run.get("state")
        if state in by_state:
            by_state[state] += 1
        tokens_in += int(run.get("tokens_in", 0) or 0)
        tokens_out += int(run.get("tokens_out", 0) or 0)
        cost_usd += float(run.get("cost_usd", 0.0) or 0.0)
        if state == "failed":
            failures.append(
                {"run_id": run.get("run_id"), "last_error": last_error_line(run)}
            )
    return {
        "total_runs": len(runs),
        "by_state": by_state,
        "total_tokens_in": tokens_in,
        "total_tokens_out": tokens_out,
        "total_cost_usd": round(cost_usd, 6),
        "failures": failures,
    }


def report_markdown(rep):
    lines = ["# AgentOps Report", ""]
    lines.append(f"Total runs: {rep['total_runs']}")
    lines.append("")
    lines.append("## Runs by state")
    for state in VALID_STATES:
        lines.append(f"- {state}: {rep['by_state'][state]}")
    lines.append("")
    lines.append("## Totals")
    lines.append(f"- Tokens in: {rep['total_tokens_in']}")
    lines.append(f"- Tokens out: {rep['total_tokens_out']}")
    lines.append(f"- Cost (USD): {rep['total_cost_usd']:.6f}")
    lines.append("")
    lines.append("## Failures")
    if rep["failures"]:
        for failure in rep["failures"]:
            lines.append(f"- {failure['run_id']}: {failure['last_error']}")
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def cmd_report(args):
    store = load_store(args.store)
    rep = build_report(store)
    if args.format == "json":
        text = json.dumps(rep, indent=2) + "\n"
    else:
        text = report_markdown(rep)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote report to {args.out}")
    else:
        sys.stdout.write(text)
    return 0


def cmd_list(args):
    store = load_store(args.store)
    header = f"{'RUN ID':<24} {'AGENT':<16} {'STATE':<10} {'TOKENS IN':>10} {'TOKENS OUT':>11} {'COST USD':>10}"
    print(header)
    for run_id in sorted(store):
        run = store[run_id]
        print(
            f"{run_id:<24} {str(run.get('agent') or '-'): <16} "
            f"{run.get('state', '-'): <10} "
            f"{int(run.get('tokens_in', 0) or 0):>10} "
            f"{int(run.get('tokens_out', 0) or 0):>11} "
            f"{float(run.get('cost_usd', 0.0) or 0.0):>10.6f}"
        )
    return 0


def nonneg_int(value):
    ivalue = int(value)
    if ivalue < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return ivalue


def nonneg_float(value):
    fvalue = float(value)
    if fvalue < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    return fvalue


def build_parser():
    parser = argparse.ArgumentParser(
        prog="agentops",
        description="Ops CLI for agent runs: register, log, status, cost tracking, and rollup reports.",
    )
    parser.add_argument("--store", default=None, help="path to the JSON store")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("register", help="create a new run")
    p.add_argument("run_id", help="unique run id")
    p.add_argument("--agent", default=None, help="agent name")
    p.add_argument("--meta", nargs="*", default=[], help="metadata as k=v pairs")
    p.set_defaults(func=cmd_register)

    p = sub.add_parser("log", help="append a log entry to a run")
    p.add_argument("run_id", help="run id")
    p.add_argument("message", help="log message")
    p.add_argument("--level", choices=VALID_LEVELS, default="info")
    p.set_defaults(func=cmd_log)

    p = sub.add_parser("status", help="show status, or set state")
    p.add_argument("run_id", help="run id")
    p.add_argument("--state", choices=VALID_STATES, default=None)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("cost", help="record token usage and cost on a run")
    p.add_argument("run_id", help="run id")
    p.add_argument("--tokens-in", type=nonneg_int, required=True)
    p.add_argument("--tokens-out", type=nonneg_int, required=True)
    p.add_argument("--cost-usd", type=nonneg_float, default=None)
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("report", help="rollup across all runs")
    p.add_argument("--format", choices=("md", "json"), default="md")
    p.add_argument("--out", default=None, help="write report to file instead of stdout")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("list", help="compact table of runs")
    p.set_defaults(func=cmd_list)

    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.store is None:
        args.store = default_store_path()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"agentops: error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
