#!/usr/bin/env python3
"""End-to-end tests for the agentops CLI. Runs the real CLI in a temp dir.

Usage: python3 tests/test_agentops.py
"""

import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLI = os.path.join(ROOT, "agentops.py")


def run(args, store, cwd):
    return subprocess.run(
        [sys.executable, CLI, "--store", store] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def read_store(store):
    with open(store, "r", encoding="utf-8") as fh:
        return json.load(fh)


results = []
passed = 0


def check(name, fn):
    global passed
    try:
        fn()
    except AssertionError as exc:
        results.append(f"FAIL {name}: {exc}")
    except Exception as exc:  # noqa: BLE001
        results.append(f"ERROR {name}: {type(exc).__name__}: {exc}")
    else:
        passed += 1
        results.append(f"ok   {name}")


def fresh():
    tmp = tempfile.mkdtemp(prefix="agentops-test-")
    return tmp, os.path.join(tmp, "store.json")


def test_register_log_status_cost_roundtrip():
    tmp, store = fresh()
    r = run(["register", "r1", "--agent", "agent-a", "--meta", "team=x", "prio=1"], store, tmp)
    assert r.returncode == 0, r.stderr
    data = read_store(store)
    assert data["r1"]["state"] == "running"
    assert data["r1"]["agent"] == "agent-a"
    assert data["r1"]["meta"] == {"team": "x", "prio": "1"}
    assert data["r1"]["started_at"], "started_at missing"
    assert data["r1"]["ended_at"] is None

    r = run(["log", "r1", "hello world", "--level", "info"], store, tmp)
    assert r.returncode == 0, r.stderr
    r = run(["log", "r1", "something bad", "--level", "error"], store, tmp)
    assert r.returncode == 0, r.stderr
    data = read_store(store)
    assert len(data["r1"]["logs"]) == 2
    assert data["r1"]["logs"][0]["message"] == "hello world"
    assert data["r1"]["logs"][1]["level"] == "error"
    assert all(e["ts"] for e in data["r1"]["logs"])

    r = run(["status", "r1"], store, tmp)
    assert r.returncode == 0, r.stderr
    assert "running" in r.stdout

    r = run(["cost", "r1", "--tokens-in", "100", "--tokens-out", "50", "--cost-usd", "0.0123"], store, tmp)
    assert r.returncode == 0, r.stderr
    data = read_store(store)
    assert data["r1"]["tokens_in"] == 100
    assert data["r1"]["tokens_out"] == 50
    assert abs(data["r1"]["cost_usd"] - 0.0123) < 1e-9

    r = run(["status", "r1", "--state", "succeeded"], store, tmp)
    assert r.returncode == 0, r.stderr
    data = read_store(store)
    assert data["r1"]["state"] == "succeeded"
    assert data["r1"]["ended_at"], "ended_at missing after succeed"

    r = run(["status", "r1", "--state", "failed"], store, tmp)
    assert r.returncode == 0, r.stderr
    data = read_store(store)
    assert data["r1"]["state"] == "failed"
    assert data["r1"]["ended_at"], "ended_at missing after fail"


def test_duplicate_register_errors():
    tmp, store = fresh()
    r = run(["register", "dup"], store, tmp)
    assert r.returncode == 0, r.stderr
    r = run(["register", "dup"], store, tmp)
    assert r.returncode != 0, "expected non-zero exit on duplicate register"
    assert "already exists" in r.stderr, r.stderr
    # store still valid JSON with exactly one run
    data = read_store(store)
    assert list(data) == ["dup"]


def test_log_unknown_run_errors():
    tmp, store = fresh()
    r = run(["log", "nope", "message"], store, tmp)
    assert r.returncode != 0, "expected non-zero exit logging to unknown run"
    assert "unknown run id" in r.stderr, r.stderr


def test_status_unknown_run_errors():
    tmp, store = fresh()
    r = run(["status", "nope"], store, tmp)
    assert r.returncode != 0
    assert "unknown run id" in r.stderr, r.stderr


def test_cost_unknown_run_errors():
    tmp, store = fresh()
    r = run(["cost", "nope", "--tokens-in", "1", "--tokens-out", "1"], store, tmp)
    assert r.returncode != 0
    assert "unknown run id" in r.stderr, r.stderr


def test_report_math():
    tmp, store = fresh()
    run(["register", "a1"], store, tmp)
    run(["register", "a2"], store, tmp)
    run(["register", "a3"], store, tmp)
    run(["cost", "a1", "--tokens-in", "100", "--tokens-out", "50", "--cost-usd", "0.01"], store, tmp)
    run(["cost", "a2", "--tokens-in", "200", "--tokens-out", "75", "--cost-usd", "0.02"], store, tmp)
    run(["cost", "a3", "--tokens-in", "300", "--tokens-out", "25"], store, tmp)  # no cost-usd
    run(["status", "a1", "--state", "succeeded"], store, tmp)
    run(["status", "a2", "--state", "failed"], store, tmp)

    r = run(["report", "--format", "json"], store, tmp)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["total_runs"] == 3, rep
    assert rep["by_state"]["succeeded"] == 1, rep
    assert rep["by_state"]["failed"] == 1, rep
    assert rep["by_state"]["running"] == 1, rep
    assert rep["total_tokens_in"] == 600, rep
    assert rep["total_tokens_out"] == 150, rep
    assert abs(rep["total_cost_usd"] - 0.03) < 1e-9, rep


def test_failure_listing_with_last_error():
    tmp, store = fresh()
    run(["register", "f1"], store, tmp)
    run(["log", "f1", "starting"], store, tmp)
    run(["log", "f1", "boom: out of memory", "--level", "error"], store, tmp)
    run(["status", "f1", "--state", "failed"], store, tmp)
    run(["register", "f2"], store, tmp)
    run(["log", "f2", "just a warn", "--level", "warn"], store, tmp)
    run(["status", "f2", "--state", "failed"], store, tmp)

    r = run(["report", "--format", "json"], store, tmp)
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert len(rep["failures"]) == 2, rep
    by_id = {f["run_id"]: f["last_error"] for f in rep["failures"]}
    assert by_id["f1"] == "boom: out of memory", by_id
    assert by_id["f2"] == "just a warn", by_id  # falls back to last log line

    r = run(["report", "--format", "md"], store, tmp)
    assert r.returncode == 0, r.stderr
    assert "f1" in r.stdout and "boom: out of memory" in r.stdout, r.stdout
    assert "Total runs: 2" in r.stdout, r.stdout


def test_store_atomicity_valid_json_after_operations():
    tmp, store = fresh()
    ops = [
        ["register", "w1"],
        ["register", "w2", "--agent", "x"],
        ["log", "w1", "m1"],
        ["log", "w2", "m2", "--level", "warn"],
        ["cost", "w1", "--tokens-in", "10", "--tokens-out", "5", "--cost-usd", "0.001"],
        ["status", "w2", "--state", "failed"],
        ["log", "w2", "m3"],
        ["report", "--format", "json"],
        ["list"],
    ]
    for args in ops:
        r = run(args, store, tmp)
        assert r.returncode == 0, f"{args}: {r.stderr}"
        # store must be parseable JSON after every mutating op
        data = read_store(store)
        assert isinstance(data, dict)
    # no leftover temp files from atomic writes
    store_dir = os.path.dirname(store)
    leftovers = [f for f in os.listdir(store_dir) if f.endswith(".tmp")]
    assert not leftovers, leftovers


def test_report_to_file():
    tmp, store = fresh()
    run(["register", "o1"], store, tmp)
    out = os.path.join(tmp, "report.md")
    r = run(["report", "--out", out], store, tmp)
    assert r.returncode == 0, r.stderr
    with open(out, "r", encoding="utf-8") as fh:
        content = fh.read()
    assert "# AgentOps Report" in content
    assert "Total runs: 1" in content


def test_list_compact_table():
    tmp, store = fresh()
    run(["register", "l1", "--agent", "aa"], store, tmp)
    run(["register", "l2"], store, tmp)
    run(["cost", "l1", "--tokens-in", "7", "--tokens-out", "3"], store, tmp)
    r = run(["list"], store, tmp)
    assert r.returncode == 0, r.stderr
    assert "l1" in r.stdout and "l2" in r.stdout, r.stdout
    assert "RUN ID" in r.stdout, r.stdout


def test_env_var_store_override():
    tmp = tempfile.mkdtemp(prefix="agentops-test-env-")
    env_store = os.path.join(tmp, "env-store.json")
    env = dict(os.environ, AGENTOPS_STORE=env_store)
    r = subprocess.run(
        [sys.executable, CLI, "register", "e1"],
        cwd=tmp,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert os.path.exists(env_store), "AGENTOPS_STORE env var was not honored"
    data = read_store(env_store)
    assert "e1" in data


def test_bad_meta_format_errors():
    tmp, store = fresh()
    r = run(["register", "m1", "--meta", "notkv"], store, tmp)
    assert r.returncode != 0
    assert "k=v" in r.stderr, r.stderr


def test_empty_run_id_rejected():
    tmp, store = fresh()
    r = run(["register", ""], store, tmp)
    assert r.returncode != 0
    assert "non-empty" in r.stderr, r.stderr
    for args in (
        ["log", "", "msg"],
        ["status", ""],
        ["cost", "", "--tokens-in", "1", "--tokens-out", "1"],
    ):
        r = run(args, store, tmp)
        assert r.returncode != 0, args
        assert "non-empty" in r.stderr, (args, r.stderr)
    # nothing was written to the store
    assert not os.path.exists(store)


def test_whitespace_run_id_rejected():
    tmp, store = fresh()
    r = run(["register", "   "], store, tmp)
    assert r.returncode != 0
    assert "non-empty" in r.stderr, r.stderr


def test_corrupt_store_top_level_errors():
    tmp, store = fresh()
    with open(store, "w", encoding="utf-8") as fh:
        fh.write("[1, 2, 3]")
    r = run(["list"], store, tmp)
    assert r.returncode != 0
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr


def test_corrupt_store_entry_errors():
    tmp, store = fresh()
    with open(store, "w", encoding="utf-8") as fh:
        json.dump({"r1": "not-an-object"}, fh)
    r = run(["list"], store, tmp)
    assert r.returncode != 0
    assert "corrupt" in r.stderr, r.stderr
    assert "Traceback" not in r.stderr, r.stderr
    r = run(["report", "--format", "json"], store, tmp)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr


def test_malformed_json_store_errors_cleanly():
    tmp, store = fresh()
    with open(store, "w", encoding="utf-8") as fh:
        fh.write("{not valid json")
    r = run(["list"], store, tmp)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert "agentops: error:" in r.stderr, r.stderr


def test_store_path_is_directory_errors_cleanly():
    tmp, store = fresh()
    os.makedirs(store)
    r = run(["list"], store, tmp)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr
    assert "agentops: error:" in r.stderr, r.stderr


def test_report_out_creates_parent_dirs():
    tmp, store = fresh()
    run(["register", "p1"], store, tmp)
    out = os.path.join(tmp, "nested", "deep", "report.md")
    r = run(["report", "--out", out], store, tmp)
    assert r.returncode == 0, r.stderr
    with open(out, "r", encoding="utf-8") as fh:
        assert "# AgentOps Report" in fh.read()


def test_list_truncates_long_ids():
    tmp, store = fresh()
    long_id = "r-" + "x" * 60
    long_agent = "agent-" + "y" * 40
    r = run(["register", long_id, "--agent", long_agent], store, tmp)
    assert r.returncode == 0, r.stderr
    r = run(["list"], store, tmp)
    assert r.returncode == 0, r.stderr
    assert "..." in r.stdout, r.stdout
    assert long_id not in r.stdout, "full 62-char id should be truncated"
    assert long_agent not in r.stdout, "full agent name should be truncated"
    for line in r.stdout.splitlines():
        assert len(line) <= 24 + 1 + 16 + 1 + 10 + 1 + 10 + 1 + 11 + 1 + 10, line


def test_negative_tokens_rejected():
    tmp, store = fresh()
    run(["register", "n1"], store, tmp)
    r = run(["cost", "n1", "--tokens-in", "-1", "--tokens-out", "0"], store, tmp)
    assert r.returncode != 0
    assert "must be >= 0" in r.stderr, r.stderr
    r = run(["cost", "n1", "--tokens-in", "0", "--tokens-out", "0", "--cost-usd", "-0.5"], store, tmp)
    assert r.returncode != 0
    assert "must be >= 0" in r.stderr, r.stderr


def test_noninteger_tokens_rejected():
    tmp, store = fresh()
    run(["register", "n2"], store, tmp)
    r = run(["cost", "n2", "--tokens-in", "abc", "--tokens-out", "0"], store, tmp)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr


def test_bad_level_choice_rejected():
    tmp, store = fresh()
    run(["register", "n3"], store, tmp)
    r = run(["log", "n3", "msg", "--level", "bogus"], store, tmp)
    assert r.returncode != 0
    assert "Traceback" not in r.stderr, r.stderr


def test_help_shows_examples():
    r = subprocess.run(
        [sys.executable, CLI, "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "examples" in r.stdout.lower(), r.stdout
    assert "agentops register" in r.stdout, r.stdout


def test_version_flag():
    r = subprocess.run(
        [sys.executable, CLI, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert r.returncode == 0, r.stderr
    assert "agentops" in r.stdout, r.stdout


def test_status_running_clears_ended_at():
    tmp, store = fresh()
    run(["register", "e1"], store, tmp)
    run(["status", "e1", "--state", "succeeded"], store, tmp)
    data = read_store(store)
    assert data["e1"]["ended_at"], "ended_at should be set after succeeded"
    run(["status", "e1", "--state", "running"], store, tmp)
    data = read_store(store)
    assert data["e1"]["state"] == "running"
    assert data["e1"]["ended_at"] is None, "ended_at should be cleared when back to running"


if __name__ == "__main__":
    for name, fn in sorted(
        [(k, v) for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    ):
        check(name, fn)

    print("\n".join(results))
    print(f"\n{passed} passed, {len(results) - passed} failed/errors out of {len(results)}")
    sys.exit(0 if passed == len(results) else 1)
