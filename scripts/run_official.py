"""Run official public tooling from its own directory, importing our root Agent entry point."""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def main():
    root = Path(__file__).resolve().parents[1]
    load_dotenv(root / ".env")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["local_eval", "make_submission"])
    args, forwarded = parser.parse_known_args()
    kit = Path(os.getenv("PARTICIPANT_KIT_DIR", "data/participant-kit"))
    if not kit.is_absolute():
        kit = root / kit
    script = kit / f"{args.command}.py"
    if not script.is_file():
        parser.error("Import the participant ZIP first; see README.md")
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join([str(root), str(root / "packages"),
                                       env.get("PYTHONPATH", "")])
    completed = subprocess.run([sys.executable, str(script), *forwarded], cwd=kit, env=env)
    if completed.returncode == 0 and args.command == "make_submission":
        (root / "artifacts").mkdir(exist_ok=True)
        shutil.copyfile(kit / "submission.csv", root / "artifacts/submission.csv")
        print("Saved artifacts/submission.csv")
    raise SystemExit(completed.returncode)


if __name__ == "__main__":
    main()
