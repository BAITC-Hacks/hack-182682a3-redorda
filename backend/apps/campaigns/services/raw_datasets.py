"""Import four raw CSVs without inventing customer profiles or campaign results."""

import hashlib
import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.campaigns.models import Dataset

from .datasets import (
    REQUIRED_COLUMNS,
    SEGMENTS,
    TARIFF_NUMBERS,
    TRAFFIC_NUMBERS,
    DatasetValidationError,
    number,
    optional_numbers,
    read_csv,
)

RAW_FILENAMES = ("dict_tariff.csv", "traffic.csv", "arpu_monthly.csv", "change_tariff.csv")
MAX_FILE_BYTES = 30 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024


def validate_raw_dataset(source):
    """Count distinct traffic subscribers; historical IDs need not share a population."""
    tariffs, ids, file_rows = set(), set(), {}
    for name in RAW_FILENAMES:
        try:
            count = 0
            for row_number, row in read_csv(source / name, REQUIRED_COLUMNS[f"data/{name}"]):
                count += 1
                if name == "dict_tariff.csv":
                    code = row["tariff_plan_code"].strip()
                    if not code or code in tariffs:
                        raise DatasetValidationError(
                            f"Пустой или повторяющийся код тарифа, строка {row_number}."
                        )
                    tariffs.add(code)
                    number(row["price_tariff"], "price_tariff", row_number, nonnegative=True)
                    optional_numbers(row, TARIFF_NUMBERS - {"price_tariff"}, row_number,
                                     nonnegative=True)
                    continue
                customer_id = row["ID_NUMBER"].strip()
                if not customer_id:
                    raise DatasetValidationError(f"Пустой ID_NUMBER, строка {row_number}.")
                if name == "traffic.csv":
                    ids.add(customer_id)
                    optional_numbers(row, TRAFFIC_NUMBERS, row_number)
                    tariff_columns = ("tariff_plan_code",)
                elif name == "arpu_monthly.csv":
                    number(row["ARPU_1M"], "ARPU_1M", row_number)
                    tariff_columns = ()
                else:
                    for column in ("AVG_ARPU_PREV_3M", "AVG_ARPU_NEXT_3M"):
                        number(row[column], column, row_number)
                    tariff_columns = ("tariff_plan_code_from", "tariff_plan_code_to")
                for column in tariff_columns:
                    value = row[column].strip()
                    if value and value not in tariffs:
                        raise DatasetValidationError(
                            f"Неизвестный тариф в {column}, строка {row_number}."
                        )
            if not count:
                raise DatasetValidationError("CSV не содержит строк данных.")
            file_rows[name] = count
        except DatasetValidationError as exc:
            raise DatasetValidationError(f"Ошибка в {name}: {exc}") from exc
    return len(ids), {
        "baseline_arpu": None,
        "tariff_count": len(tariffs),
        "segments": {name: {} for name in SEGMENTS},
        "file_rows": file_rows,
        "format": "raw_csv",
    }


def import_raw_dataset(files, *, source_kind):
    """Persist one immutable copy; concurrent duplicates only remove their own copies."""
    names = [file.name for file in files]
    if len(names) != len(RAW_FILENAMES) or set(names) != set(RAW_FILENAMES):
        raise DatasetValidationError("Загрузите ровно четыре CSV с требуемыми именами файлов.")
    if any(not file.size or file.size > MAX_FILE_BYTES for file in files):
        raise DatasetValidationError("Каждый CSV должен быть непустым и не больше 30 МиБ.")
    if sum(file.size for file in files) > MAX_TOTAL_BYTES:
        raise DatasetValidationError("Общий размер четырёх CSV не должен превышать 50 МиБ.")

    root = Path(settings.MEDIA_ROOT) / "datasets"
    root.mkdir(parents=True, exist_ok=True)
    source = Path(tempfile.mkdtemp(prefix="import-", dir=root)).resolve()
    keep = False
    try:
        data = source / "data"
        data.mkdir()
        digest = hashlib.sha256(b"redorda-raw-csv-v1\0")
        total = 0
        by_name = {file.name: file for file in files}
        for name in RAW_FILENAMES:
            digest.update(name.encode())
            digest.update(b"\0")
            file_size = 0
            file_digest = hashlib.sha256()
            with (data / name).open("xb") as destination:
                for chunk in by_name[name].chunks():
                    file_size += len(chunk)
                    total += len(chunk)
                    if file_size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                        raise DatasetValidationError("Превышен допустимый размер CSV.")
                    destination.write(chunk)
                    file_digest.update(chunk)
            digest.update(file_digest.digest())
        customer_count, summary = validate_raw_dataset(data)
        summary.update(source_kind=source_kind, synthetic=True if source_kind == "demo" else None)
        with transaction.atomic():
            dataset, created = Dataset.objects.get_or_create(
                checksum=digest.hexdigest(),
                defaults={
                    "name": "Beeline · демоданные" if source_kind == "demo" else "Загруженные CSV",
                    "source_dir": str(source),
                    "customer_count": customer_count,
                    "summary": summary,
                },
            )
            if not created:
                # Preserve original provenance, stored files and all historical run references.
                Dataset.objects.filter(pk=dataset.pk).update(imported_at=timezone.now())
                dataset.refresh_from_db()
        keep = created
        return dataset, created
    finally:
        if not keep:
            shutil.rmtree(source)
