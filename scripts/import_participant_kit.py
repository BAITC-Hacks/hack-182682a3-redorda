"""Extract the known public participant bundle; do not execute organizer code here."""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZipFile

EXPECTED_SHA256 = "df1d955fb97816ff6de8ceb142ed589915d969f43f840a650dcdfbe12734f20b"
FILES = (
    "scoring_core.py", "make_submission.py", "customer_profile.csv", "tariff_dictionary.csv",
    "feature_dictionary.csv", "local_eval.py", "environment.py", "mock_environment.py",
    "PARTICIPANT_GUIDE.md", "PARTICIPANT_GUIDE.pdf", "agent_template.py",
    "data/dict_tariff.csv", "data/traffic.csv", "data/arpu_monthly.csv", "data/change_tariff.csv",
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--destination", type=Path,
                        default=Path(__file__).resolve().parents[1] / "data/participant-kit")
    parser.add_argument("--expected-sha256", default=EXPECTED_SHA256)
    args = parser.parse_args()
    checksum = hashlib.sha256(args.archive.read_bytes()).hexdigest()
    if checksum != args.expected_sha256:
        parser.error(f"Archive checksum differs: {checksum}. Verify the organizer's new bundle.")
    with ZipFile(args.archive) as archive:
        missing = set(FILES) - set(archive.namelist())
        if missing:
            parser.error(f"Missing participant files: {sorted(missing)}")
        for name in FILES:
            destination = args.destination / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(archive.read(name))
    print(f"Imported {len(FILES)} unmodified participant files to {args.destination}")


if __name__ == "__main__":
    main()
