"""Domain intake node: user message (+ optional image) -> redacted text.

Pipeline:
  1. If the user attached an image, OCR it with pytesseract. If OCR confidence
     is low, fall back to the LLM's vision (kimi-k2.7-code:cloud is multimodal).
  2. Redact PII using the policy's patterns.
  3. Put the redacted text in state so the planner only ever sees scrubbed text.

OCR is optional: if pytesseract or the Tesseract binary isn't installed, the
node logs the route and falls back to plain text. That keeps the notebook
runnable on machines without Tesseract.
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage

from governance.audit import AuditLogger
from governance.pii import redact
from governance.policy import Policy


def _ocr_text(image_path: str | Path) -> tuple[str, float]:
    """Run pytesseract, return (text, median per-word confidence).

    median (not mean) because garbage words skew the mean down and trigger the
    vision fallback too eagerly.
    """
    import pytesseract  # lazy import so the module imports without tesseract
    from PIL import Image

    img = Image.open(image_path)
    data = pytesseract.image_to_data(img, output_type=pytesseract.Output.DICT)
    confs = [int(c) for c in data["conf"] if c not in ("-1", -1, None)]
    text = " ".join(w for w in data["text"] if w.strip())
    conf = statistics.median(confs) if confs else 0.0
    return text, float(conf)


def _vision_text(llm, image_path: str | Path) -> str:
    """Ask the multimodal LLM to read the image. Returns text or '' on failure."""
    import base64

    # kimi-k2.7-code:cloud accepts image content blocks via the ollama adapter.
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    msg = HumanMessage(content=[
        {"type": "text", "text": "Read the text in this image and return it verbatim."},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
    ])
    try:
        resp = llm.invoke([msg])
        return resp.content if hasattr(resp, "content") else str(resp)
    except Exception:
        return ""


def make_intake_node(policy: Policy, audit: AuditLogger, llm=None, ocr_threshold: int = 65):
    def intake(state: dict) -> dict:
        user_text = state.get("redacted_text", "") or ""
        image_path = state.get("domain_state", {}).get("image_path")

        if image_path:
            try:
                text, conf = _ocr_text(image_path)
                if conf < ocr_threshold and llm is not None:
                    vision_text = _vision_text(llm, image_path)
                    if vision_text:
                        text = vision_text
                        audit.log_decision(turn_id=state["turn_id"], node="ingest",
                                            decision="ocr_route:vision_fallback",
                                            rationale=f"ocr_conf={conf:.0f}<{ocr_threshold}")
                    else:
                        audit.log_decision(turn_id=state["turn_id"], node="ingest",
                                            decision="ocr_route:ocr_low_conf_no_vision",
                                            rationale=f"ocr_conf={conf:.0f}")
                else:
                    audit.log_decision(turn_id=state["turn_id"], node="ingest",
                                       decision="ocr_route:ocr",
                                       rationale=f"ocr_conf={conf:.0f}")
            except Exception as e:
                # Tesseract missing / image unreadable — fall back to plain text.
                audit.log_decision(turn_id=state["turn_id"], node="ingest",
                                    decision="ocr_route:unavailable",
                                    rationale=str(e)[:120])
                text = user_text
        else:
            text = user_text

        redacted = redact(text, policy)
        return {"redacted_text": redacted, "messages": state.get("messages", [])}
    return intake


if __name__ == "__main__":
    # Self-check: text-only intake redacts a fake SSN.
    import tempfile

    from governance.policy import Policy as P

    pol = P.from_dict({
        "domain": "self_test",
        "pii_redaction": {"enabled": True, "patterns": [
            {"name": "ssn", "regex": r"\b\d{3}-\d{2}-\d{4}\b", "replacement": "[SSN-REDACTED]"}]},
        "actions": [],
    })
    with tempfile.TemporaryDirectory() as d:
        audit = AuditLogger(Path(d) / "audit.jsonl")
        node = make_intake_node(pol, audit)
        out = node({"redacted_text": "Contact me at 123-45-6789 about unit 4B",
                    "turn_id": "t1", "messages": []})
        assert "[SSN-REDACTED]" in out["redacted_text"], out
        assert "123-45-6789" not in out["redacted_text"], out
        print("domain/intake_node.py self-check OK")