"""Validate hypothesis generation with one API request."""

import json
import os
from pathlib import Path

import pandas as pd
from campaign_engine.candidates import build_candidates, load_history
from campaign_engine.openai_gateway import PlanningUnavailable, make_replay, propose_hypotheses
from campaign_engine.runner import public_planning_summary
from campaign_engine.segments import SegmentIndex
from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    kit = Path(os.getenv("PARTICIPANT_KIT_DIR", "data/participant-kit"))
    if not kit.is_absolute():
        kit = root / kit
    index = SegmentIndex(pd.read_csv(kit / "customer_profile.csv"))
    tariffs = pd.read_csv(kit / "data/dict_tariff.csv")
    candidates = build_candidates(index, tariffs, load_history(kit))
    summary = public_planning_summary(index, tariffs, candidates)
    try:
        batch = propose_hypotheses(summary, timeout_seconds=30)
    except PlanningUnavailable as exc:
        print(json.dumps({"status": "unavailable", "message": str(exc),
                          "code": getattr(exc, "code", "unavailable"),
                          "http_status": getattr(exc, "http_status", None)}))
        raise SystemExit(2) from None
    output = root / "artifacts/openai_hypotheses_replay.json"
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(make_replay(summary, batch), ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"status": "ok", "hypotheses": len(batch.hypotheses),
                      "replay": str(output)}))


if __name__ == "__main__":
    main()
