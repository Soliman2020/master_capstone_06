"""Tests for governance/audit.py — hash-chain integrity and resume."""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from governance.audit import AuditLogger, ChainBrokenError, GENESIS_HASH  # noqa: E402


def _new_log(d: Path) -> AuditLogger:
    return AuditLogger(d / "audit.jsonl")


def test_empty_log_verifies():
    with tempfile.TemporaryDirectory() as d:
        assert _new_log(Path(d)).verify_chain() is True


def test_chain_grows_and_verifies():
    with tempfile.TemporaryDirectory() as d:
        log = _new_log(Path(d))
        log.log_decision(turn_id="t1", node="planner", decision="plan_issued")
        log.log_call(turn_id="t1", action="a.read", args={"q": 1},
                     tool="a.read", result_summary="ok")
        log.log_block(turn_id="t1", action="a.forbidden", args={},
                      violations=["action_not_allowed"], block_reason="no")
        log.log_human_approval(turn_id="t1", action="a.renew",
                               approver="manager", granted=True, note="ok")
        assert len(log.read_all()) == 4
        assert log.verify_chain() is True


def test_each_line_links_prev_hash():
    with tempfile.TemporaryDirectory() as d:
        log = _new_log(Path(d))
        log.log_decision(turn_id="t1", node="planner", decision="d1")
        log.log_decision(turn_id="t1", node="planner", decision="d2")
        lines = log.read_all()
        assert lines[0]["prev_hash"] == GENESIS_HASH
        assert lines[1]["prev_hash"] == lines[0]["this_hash"]
        assert lines[0]["this_hash"] != lines[1]["this_hash"]


def test_tampering_breaks_chain():
    with tempfile.TemporaryDirectory() as d:
        log = _new_log(Path(d))
        log.log_decision(turn_id="t1", node="planner", decision="d1")
        log.log_block(turn_id="t1", action="a.forbidden", args={},
                      violations=["action_not_allowed"], block_reason="original")
        log.log_call(turn_id="t1", action="a.read", args={}, tool="a.read",
                     result_summary="ok")

        path = Path(d) / "audit.jsonl"
        lines = path.read_text(encoding="utf-8").splitlines()
        mid = json.loads(lines[1])
        mid["block_reason"] = "tampered"
        lines[1] = json.dumps(mid, ensure_ascii=False)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

        try:
            log.verify_chain()
        except ChainBrokenError:
            pass
        else:
            raise AssertionError("tampering not detected")


def test_resume_continues_chain():
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "audit.jsonl"
        log1 = AuditLogger(path)
        log1.log_decision(turn_id="t1", node="planner", decision="d1")
        last_hash = log1.read_all()[-1]["this_hash"]

        # New instance on the same file should continue, not restart.
        log2 = AuditLogger(path)
        assert log2._prev_hash == last_hash
        log2.log_decision(turn_id="t1", node="planner", decision="d2")
        assert log2.read_all()[-1]["prev_hash"] == last_hash
        assert log2.verify_chain() is True


def test_seq_is_monotonic():
    with tempfile.TemporaryDirectory() as d:
        log = _new_log(Path(d))
        log.log_decision(turn_id="t1", node="planner", decision="d1")
        log.log_decision(turn_id="t1", node="planner", decision="d2")
        assert [r["seq"] for r in log.read_all()] == [0, 1]