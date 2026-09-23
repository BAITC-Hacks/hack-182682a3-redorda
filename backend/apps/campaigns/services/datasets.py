"""Validate the public participant CSVs without modifying organizer files."""

import csv
import hashlib
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED_COLUMNS = {
    "customer_profile.csv": {"ID_NUMBER", "predicted_arpu", "current_tariff",
                             "arpu_segment", "data_segment", "call_segment"},
    "tariff_dictionary.csv": {"tariff_plan_code", "price_tariff"},
    "feature_dictionary.csv": {"feature", "unit", "description"},
    "data/dict_tariff.csv": {"tariff_plan_code", "price_tariff"},
    "data/traffic.csv": {"ID_NUMBER", "time_key", "tariff_plan_code"},
    "data/arpu_monthly.csv": {"ID_NUMBER", "TIME_KEY", "ARPU_1M"},
    "data/change_tariff.csv": {"ID_NUMBER", "TIME_KEY", "tariff_plan_code_from",
                               "tariff_plan_code_to", "AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"},
}
SEGMENTS = {
    "arpu_segment": {"LOW", "MID", "HIGH"},
    "data_segment": {"NON_USER", "LITE", "HEAVY"},
    "call_segment": {"LOW", "MEDIUM", "HIGH"},
}
PROFILE_NUMBERS = {"ARPU_current", "ARPU_3m_avg", "OUT_LOC_ONNET_MIN",
                   "OUT_LOC_OFFNET_MIN", "OUT_LOC_OFFNET_UNPAID_MIN", "OUT_LOC_OFFNET_PAID_MIN",
                   "OUT_INTER_MIN", "OUT_LOC_LAND_MIN", "OUT_LOCAL_ONNET_SMS_AMT",
                   "OUT_LOCAL_OFFNET_SMS_AMT", "OUT_LOCAL_LAND_PAID_SMS_AMT", "OUT_INTER_SMS_AMT",
                   "DATA_VOLUME", "LTE_DATA_VOLUME", "TOTAL_ROAM_CALL_AMT",
                   "TOTAL_ROAM_SMS_AMT", "TOTAL_ROAM_GPRS_MB", "COUNT_CONTACT",
                   "AVG_TRANSACT_CONTACT", "SUM_TRANSACT_CONTACT", "AVG_DURATION_CONTACT",
                   "COUNT_BASE_STATION"}
TARIFF_NUMBERS = {"Data_in_PKG", "Min_another_operator_in_PKG",
                  "Min_another_operator_and_city_in_PKG", "price_tariff"}
TRAFFIC_NUMBERS = PROFILE_NUMBERS - {"ARPU_current", "ARPU_3m_avg"} | {
    "DEVICE_ID", "FIRST_DISP_DIAG", "OS_1", "OS_2", "OS_3", "OS_4"
}
REQUIRED_COLUMNS["customer_profile.csv"].update(PROFILE_NUMBERS | {"ARPU_trend"})
REQUIRED_COLUMNS["tariff_dictionary.csv"].update(TARIFF_NUMBERS | {"description"})
REQUIRED_COLUMNS["data/dict_tariff.csv"].update(TARIFF_NUMBERS)
REQUIRED_COLUMNS["data/traffic.csv"].update(TRAFFIC_NUMBERS | {"date_issue_device"})


class DatasetValidationError(ValueError):
    pass


def read_csv(path, required):
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, strict=True)
            header = reader.fieldnames or []
            if len(header) != len(set(header)) or not required.issubset(header) or "" in header:
                raise DatasetValidationError(f"Invalid or missing columns in {path.name}")
            for row_number, row in enumerate(reader, 2):
                if None in row or any(value is None for value in row.values()):
                    raise DatasetValidationError(f"Malformed {path.name} row {row_number}")
                yield row_number, row
    except (UnicodeError, csv.Error, OSError) as exc:
        raise DatasetValidationError(f"Cannot read CSV {path.name}") from exc


def number(value, label, row_number, *, nonnegative=False):
    try:
        parsed = Decimal(value)
        if not parsed.is_finite() or (nonnegative and parsed < 0):
            raise InvalidOperation
        return parsed
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise DatasetValidationError(f"Invalid {label} at row {row_number}") from exc


def optional_numbers(row, columns, row_number, *, nonnegative=False):
    for column in columns & row.keys():
        if row[column].strip():
            number(row[column], column, row_number, nonnegative=nonnegative)


def validate_dataset(source: Path):
    paths = {name: source / name for name in REQUIRED_COLUMNS}
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise DatasetValidationError(f"Missing participant CSVs: {', '.join(missing)}")

    tariffs = {}
    for row_number, row in read_csv(paths["data/dict_tariff.csv"],
                                    REQUIRED_COLUMNS["data/dict_tariff.csv"]):
        code = row["tariff_plan_code"].strip()
        if not code or code in tariffs:
            raise DatasetValidationError(f"Missing or duplicate tariff at row {row_number}")
        number(row["price_tariff"], "price_tariff", row_number, nonnegative=True)
        optional_numbers(row, TARIFF_NUMBERS - {"price_tariff"}, row_number, nonnegative=True)
        tariffs[code] = row["price_tariff"]
    if not tariffs:
        raise DatasetValidationError("Tariff dictionary is empty")

    public_tariffs = {}
    for row_number, row in read_csv(paths["tariff_dictionary.csv"],
                                    REQUIRED_COLUMNS["tariff_dictionary.csv"]):
        code = row["tariff_plan_code"].strip()
        if not code or code in public_tariffs:
            raise DatasetValidationError(f"Missing or duplicate public tariff at row {row_number}")
        number(row["price_tariff"], "price_tariff", row_number, nonnegative=True)
        optional_numbers(row, TARIFF_NUMBERS - {"price_tariff"}, row_number, nonnegative=True)
        public_tariffs[code] = row["price_tariff"]
    if set(tariffs) != set(public_tariffs):
        raise DatasetValidationError("Tariff dictionaries disagree")

    features = set()
    for row_number, row in read_csv(paths["feature_dictionary.csv"],
                                    REQUIRED_COLUMNS["feature_dictionary.csv"]):
        feature = row["feature"].strip()
        if not feature or feature in features:
            raise DatasetValidationError(f"Missing or duplicate feature at row {row_number}")
        features.add(feature)
    if not features:
        raise DatasetValidationError("Feature dictionary is empty")

    ids, total_arpu = set(), Decimal(0)
    segments = {name: Counter() for name in SEGMENTS}
    missing_current_tariff = 0
    for row_number, row in read_csv(paths["customer_profile.csv"],
                                    REQUIRED_COLUMNS["customer_profile.csv"]):
        customer_id = row["ID_NUMBER"].strip()
        if not customer_id or customer_id in ids:
            raise DatasetValidationError(f"Duplicate or missing ID_NUMBER at row {row_number}")
        ids.add(customer_id)
        tariff = row["current_tariff"].strip()
        if tariff and tariff not in tariffs:
            raise DatasetValidationError(f"Unknown tariff at row {row_number}")
        if not tariff:
            missing_current_tariff += 1
        total_arpu += number(row["predicted_arpu"], "predicted_arpu", row_number,
                             nonnegative=True)
        optional_numbers(row, PROFILE_NUMBERS, row_number)
        for name, allowed in SEGMENTS.items():
            value = row[name].strip()
            if value and value not in allowed:
                raise DatasetValidationError(f"Invalid {name} at row {row_number}")
            segments[name][value or "UNKNOWN"] += 1
    if not ids:
        raise DatasetValidationError("Profile dataset is empty")

    # Historical tables include subscribers outside the current profile. Validate structure,
    # numerical values and tariff references without treating those IDs as errors.
    for filename, number_columns, tariff_columns in (
        ("data/traffic.csv", (), ("tariff_plan_code",)),
        ("data/arpu_monthly.csv", ("ARPU_1M",), ()),
        ("data/change_tariff.csv", ("AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"),
         ("tariff_plan_code_from", "tariff_plan_code_to")),
    ):
        count = 0
        for row_number, row in read_csv(paths[filename], REQUIRED_COLUMNS[filename]):
            count += 1
            if not row["ID_NUMBER"].strip():
                raise DatasetValidationError(f"Missing ID_NUMBER in {filename} row {row_number}")
            for column in number_columns:
                number(row[column], column, row_number)
            if filename == "data/traffic.csv":
                optional_numbers(row, TRAFFIC_NUMBERS, row_number)
            for column in tariff_columns:
                value = row[column].strip()
                if value and value not in tariffs:
                    raise DatasetValidationError(f"Unknown {column} in {filename} row {row_number}")
        if not count:
            raise DatasetValidationError(f"Empty {filename}")

    digest = hashlib.sha256()
    for name, path in paths.items():
        digest.update(name.encode())
        digest.update(b"\0")
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest(), len(ids), {
        "baseline_arpu": str(total_arpu.quantize(Decimal("0.01"))),
        "tariff_count": len(tariffs),
        "segments": {name: dict(counts) for name, counts in segments.items()},
        "missing_current_tariff": missing_current_tariff,
        "synthetic": True,
    }
