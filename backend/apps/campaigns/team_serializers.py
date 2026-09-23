from decimal import Decimal

from campaign_engine.contracts import CHANNEL_COSTS
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError("Ожидается объект.")
        unknown = set(data) - set(self.fields)
        if unknown:
            raise serializers.ValidationError(
                {key: ["Неизвестное поле."] for key in sorted(unknown)}
            )
        return super().to_internal_value(data)


class ConstraintsSerializer(StrictSerializer):
    budget = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        max_value=Decimal("100000.00"),
        required=False,
    )
    allowed_channels = serializers.ListField(
        child=serializers.ChoiceField(choices=list(CHANNEL_COSTS)),
        allow_empty=False,
        required=False,
    )

    def to_internal_value(self, data):
        if isinstance(data, dict) and "budget" in data and not isinstance(data["budget"], str):
            raise serializers.ValidationError({"budget": ["Укажите decimal-строку."]})
        return super().to_internal_value(data)

    def validate_allowed_channels(self, value):
        if len(value) != len(set(value)):
            raise serializers.ValidationError("Каналы не должны повторяться.")
        return value

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("Укажите хотя бы одно ограничение.")
        return attrs


class ExplainParametersSerializer(StrictSerializer):
    campaign_id = serializers.CharField(allow_blank=False, trim_whitespace=True)


class CompareParametersSerializer(StrictSerializer):
    constraints = ConstraintsSerializer()


class CreatePlanParametersSerializer(CompareParametersSerializer):
    name = serializers.CharField(max_length=120, allow_blank=False, trim_whitespace=True)


PARAMETERS_BY_TYPE = {
    "explain": ExplainParametersSerializer,
    "compare": CompareParametersSerializer,
    "create_plan": CreatePlanParametersSerializer,
}


class TeamCommandRequestSerializer(StrictSerializer):
    type = serializers.ChoiceField(choices=list(PARAMETERS_BY_TYPE))
    snapshot_id = serializers.UUIDField()
    parameters = serializers.DictField()

    def validate_parameters(self, value):
        kind = self.initial_data.get("type")
        if kind not in PARAMETERS_BY_TYPE:
            return value
        nested = PARAMETERS_BY_TYPE[kind](data=value)
        nested.is_valid(raise_exception=True)
        return nested.validated_data


@extend_schema_field({"oneOf": [{"type": "string"}, {"type": "integer"}]})
class EvidenceIdField(serializers.JSONField):
    """Artifact IDs are strings; persisted event IDs can be integers."""


class TeamTaskSerializer(serializers.Serializer):
    id = serializers.CharField()
    actor_id = serializers.ChoiceField(
        choices=["lead", "analyst", "experiment", "finance", "control"]
    )
    status = serializers.ChoiceField(
        choices=["pending", "running", "completed", "failed", "cancelled"]
    )
    title = serializers.CharField()
    artifact_ids = serializers.ListField(child=serializers.CharField())
    evidence_ids = serializers.ListField(child=EvidenceIdField())


class TeamArtifactSerializer(serializers.Serializer):
    id = serializers.CharField()
    task_id = serializers.CharField()
    type = serializers.CharField()
    title = serializers.CharField()
    data = serializers.JSONField()
    evidence_ids = serializers.ListField(child=EvidenceIdField())


class TeamSnapshotSerializer(serializers.Serializer):
    schema_version = serializers.IntegerField()
    run_id = serializers.UUIDField()
    snapshot_id = serializers.UUIDField()
    last_event_id = serializers.IntegerField(min_value=0)
    tasks = TeamTaskSerializer(many=True)
    artifacts = TeamArtifactSerializer(many=True)
    available_commands = serializers.ListField(child=serializers.CharField())


class TeamCommandResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    type = serializers.CharField()
    status = serializers.ChoiceField(choices=["queued", "running", "completed", "failed"])
    result = serializers.JSONField(allow_null=True)
    error = serializers.JSONField(allow_null=True)
