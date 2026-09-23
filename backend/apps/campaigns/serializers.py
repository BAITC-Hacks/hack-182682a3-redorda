from campaign_engine.contracts import RunConfig
from pydantic import ValidationError as ContractError
from rest_framework import serializers

from .models import CampaignRun, Dataset


class HealthSerializer(serializers.Serializer):
    status = serializers.CharField()
    service = serializers.CharField()


class MetaSerializer(serializers.Serializer):
    limits = serializers.DictField()
    channel_costs = serializers.DictField()
    features = serializers.DictField()


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "checksum", "customer_count", "summary", "imported_at"]


class RunSerializer(serializers.ModelSerializer):
    dataset_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = CampaignRun
        fields = ["id", "name", "dataset_id", "status", "budget", "max_contacts", "max_pilots",
                  "seed", "strategy", "created_at"]
        read_only_fields = ["id", "dataset_id", "status", "created_at"]

    def validate(self, attrs):
        try:
            config = RunConfig.model_validate({
                key: value for key, value in attrs.items() if key in RunConfig.model_fields
            })
        except ContractError as exc:
            raise serializers.ValidationError({
                str(error["loc"][0]): error["msg"] for error in exc.errors()
            }) from exc
        if config.strategy != "baseline":
            raise serializers.ValidationError({
                "strategy": "Стратегия OpenAI будет доступна после подключения агента."
            })
        attrs.update(config.model_dump())
        return attrs
