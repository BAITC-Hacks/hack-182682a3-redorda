from decimal import Decimal

from rest_framework import serializers

from .models import CampaignRun, Dataset


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()


class MetaFeaturesSerializer(serializers.Serializer):
    run_execution = serializers.BooleanField()
    openai_strategy = serializers.BooleanField()
    csv_export = serializers.BooleanField()


class MetaSerializer(serializers.Serializer):
    limits = serializers.DictField(child=serializers.IntegerField())
    channel_costs = serializers.DictField(child=serializers.IntegerField())
    features = MetaFeaturesSerializer()


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "checksum", "customer_count", "summary", "imported_at"]


class RunSerializer(serializers.ModelSerializer):
    dataset_id = serializers.UUIDField(read_only=True)
    budget = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"),
                                      max_value=Decimal("100000.00"), default=Decimal("100000.00"))
    max_contacts = serializers.IntegerField(min_value=1, max_value=15000, default=15000)
    max_pilots = serializers.IntegerField(min_value=1, max_value=20, default=20)
    seed = serializers.IntegerField(min_value=0, max_value=2**31 - 1, default=42)
    strategy = serializers.ChoiceField(choices=["baseline"], default="baseline")

    class Meta:
        model = CampaignRun
        fields = ["id", "name", "dataset_id", "status", "budget", "max_contacts", "max_pilots",
                  "seed", "strategy", "created_at"]
        read_only_fields = ["id", "dataset_id", "status", "created_at"]

    def to_internal_value(self, data):
        if isinstance(data, dict):
            allowed = {"name", "budget", "max_contacts", "max_pilots", "seed", "strategy"}
            unknown = set(data) - allowed
            if unknown:
                raise serializers.ValidationError({
                    key: ["Неизвестное поле."] for key in sorted(unknown)
                })
        return super().to_internal_value(data)


class RunEventSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    kind = serializers.CharField()
    payload = serializers.JSONField()
    created_at = serializers.DateTimeField()


class RunEventPageSerializer(serializers.Serializer):
    results = RunEventSerializer(many=True)
    next_after = serializers.IntegerField()
    has_more = serializers.BooleanField()


class CampaignResultSerializer(serializers.Serializer):
    rank = serializers.IntegerField()
    parameters = serializers.DictField()
    explanation = serializers.CharField()
    metrics = serializers.DictField()


class ResultTotalsSerializer(serializers.Serializer):
    pilot_cost = serializers.CharField()
    campaign_cost = serializers.CharField(allow_null=True)
    total_cost = serializers.CharField(allow_null=True)
    pilot_contacts = serializers.IntegerField()
    total_contacts = serializers.IntegerField(allow_null=True)
    predicted_effect = serializers.JSONField(allow_null=True)
    simulator_result = serializers.JSONField(allow_null=True)


class RunResultSerializer(serializers.Serializer):
    run_id = serializers.UUIDField()
    status = serializers.ChoiceField(choices=["completed"])
    campaigns = CampaignResultSerializer(many=True)
    totals = ResultTotalsSerializer()
    warnings = serializers.ListField(child=serializers.CharField())


class ErrorDetailSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()
    fields = serializers.DictField()


class ErrorSerializer(serializers.Serializer):
    error = ErrorDetailSerializer()
