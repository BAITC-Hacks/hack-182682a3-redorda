from contextlib import ExitStack
from pathlib import Path

from django.conf import settings
from django.core.exceptions import RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent
from django.core.files import File
from django.core.files.uploadhandler import FileUploadHandler, StopUpload
from django.http.multipartparser import MultiPartParserError
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.exceptions import APIException, ParseError, UnsupportedMediaType
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import DatasetSerializer, DatasetUploadSerializer, ErrorSerializer
from .services import raw_datasets
from .services.datasets import DatasetValidationError


class InvalidDataset(APIException):
    status_code = 400
    default_detail = "Проверьте четыре CSV-файла и параметры загрузки."
    default_code = "invalid_dataset"


class DatasetImportUnavailable(APIException):
    status_code = 503
    default_detail = "Не удалось сохранить набор данных. Попробуйте позже."
    default_code = "dataset_import_unavailable"


class BoundedDatasetUploadHandler(FileUploadHandler):
    """Stop multipart file ingestion before Django buffers oversized uploads."""

    def __init__(self, request):
        super().__init__(request)
        self.total_bytes = 0
        self.file_bytes = 0
        self.file_count = 0
        self.error = None

    def reject(self, message):
        self.error = message
        raise StopUpload(connection_reset=True)

    def new_file(self, *args, **kwargs):
        super().new_file(*args, **kwargs)
        self.file_bytes = 0
        self.file_count += 1
        if self.field_name != "files" or self.file_count > 4:
            self.reject("Ожидаются ровно четыре CSV-файла в поле files.")

    def receive_data_chunk(self, raw_data, start):
        self.file_bytes += len(raw_data)
        self.total_bytes += len(raw_data)
        if self.file_bytes > raw_datasets.MAX_FILE_BYTES:
            self.reject("Размер одного CSV не должен превышать 30 МиБ.")
        if self.total_bytes > raw_datasets.MAX_TOTAL_BYTES:
            self.reject("Общий размер CSV не должен превышать 50 МиБ.")
        return raw_data

    def file_complete(self, file_size):
        return None


class DatasetImportBaseView(APIView):
    def handle_exception(self, exc):
        if isinstance(exc, (ParseError, UnsupportedMediaType, serializers.ValidationError,
                            MultiPartParserError, RequestDataTooBig, TooManyFieldsSent,
                            TooManyFilesSent)):
            exc = InvalidDataset()
        return super().handle_exception(exc)

    def import_files(self, files, source_kind):
        try:
            dataset, created = raw_datasets.import_raw_dataset(files, source_kind=source_kind)
        except DatasetValidationError as exc:
            raise InvalidDataset(str(exc)) from exc
        except OSError as exc:
            raise DatasetImportUnavailable() from exc
        return Response(DatasetSerializer(dataset).data,
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class DatasetUploadView(DatasetImportBaseView):
    parser_classes = [MultiPartParser]

    def initialize_request(self, request, *args, **kwargs):
        self.upload_limit_handler = BoundedDatasetUploadHandler(request)
        request.upload_handlers.insert(0, self.upload_limit_handler)
        return super().initialize_request(request, *args, **kwargs)

    @extend_schema(
        request=DatasetUploadSerializer,
        responses={200: DatasetSerializer, 201: DatasetSerializer, 400: ErrorSerializer,
                   403: ErrorSerializer, 503: ErrorSerializer},
        description=("Import exactly dict_tariff.csv, traffic.csv, arpu_monthly.csv and "
                     "change_tariff.csv using the repeated multipart files field. Limits: "
                     "30 MiB per file and 50 MiB total. Existing identical bytes reactivate "
                     "the dataset and preserve its original source metadata."),
    )
    def post(self, request):
        data = request.data
        if self.upload_limit_handler.error:
            raise InvalidDataset(self.upload_limit_handler.error)
        serializer = DatasetUploadSerializer(data=data)
        serializer.is_valid(raise_exception=True)
        return self.import_files(serializer.validated_data["files"], "upload")


class DatasetDemoImportView(DatasetImportBaseView):
    parser_classes = [JSONParser]

    @extend_schema(
        request=None,
        responses={200: DatasetSerializer, 201: DatasetSerializer, 400: ErrorSerializer,
                   403: ErrorSerializer, 503: ErrorSerializer},
        description="Import the four bundled demo CSVs. Send no body or an empty JSON object.",
    )
    def post(self, request):
        if request.data != {}:
            raise InvalidDataset("Демоимпорт не принимает параметры.")
        source = Path(settings.ROOT_DIR) / "data" / "demo"
        try:
            with ExitStack() as stack:
                files = [File(stack.enter_context((source / name).open("rb")), name=name)
                         for name in raw_datasets.RAW_FILENAMES]
                return self.import_files(files, "demo")
        except OSError as exc:
            raise DatasetImportUnavailable() from exc
