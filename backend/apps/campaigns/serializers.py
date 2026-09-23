from decimal import Decimal, InvalidOperation

from drf_spectacular.utils import extend_schema_field
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
    environment = serializers.DictField(child=serializers.CharField())


class DatasetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Dataset
        fields = ["id", "name", "checksum", "customer_count", "summary", "imported_at"]


class RunProgressSerializer(serializers.Serializer):
    stage = serializers.CharField()
    percent = serializers.IntegerField(min_value=0, max_value=100)
    spent_budget = serializers.CharField(allow_null=True)
    used_contacts = serializers.IntegerField(allow_null=True)
    completed_pilots = serializers.IntegerField()


class RunFailureSerializer(serializers.Serializer):
    code = serializers.CharField()
    message = serializers.CharField()


class RunSerializer(serializers.ModelSerializer):
    progress = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()
    cancellation_requested = serializers.BooleanField(source="cancel_requested", read_only=True)
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
                  "seed", "strategy", "created_at", "progress", "error",
                  "cancellation_requested"]
        read_only_fields = ["id", "dataset_id", "status", "created_at", "progress", "error",
                            "cancellation_requested"]

    @extend_schema_field(RunProgressSerializer)
    def get_progress(self, run):
        pilots = list(run.pilots.all())
        campaigns = list(run.campaign_results.all())
        completed = len(pilots)
        if run.status == CampaignRun.Status.COMPLETED:
            percent = 100
        elif run.started_at is None:
            percent = 0
        else:
            # The agent may finish before max_pilots. This is an estimate until completion.
            percent = min(95, 5 + 85 * completed // max(run.max_pilots, 1))
            if campaigns:
                percent = max(percent, 90)
        spent = sum((pilot.cost for pilot in pilots), Decimal("0.00"))
        contacts = sum(pilot.n_customers for pilot in pilots)
        for campaign in campaigns:
            metrics = campaign.metrics if isinstance(campaign.metrics, dict) else {}
            cost = metrics.get("cost")
            if cost is None:
                spent = None
            elif spent is not None:
                try:
                    amount = Decimal(str(cost))
                    spent = spent + amount if amount.is_finite() and amount >= 0 else None
                except (InvalidOperation, TypeError, ValueError):
                    spent = None
            count = metrics.get("n_contacts")
            if type(count) is not int or count < 0:
                contacts = None
            elif contacts is not None:
                contacts += count
        return {"stage": "finalizing" if run.status == CampaignRun.Status.RUNNING and campaigns
                else run.status, "percent": percent,
                "spent_budget": f"{spent:.2f}" if spent is not None else None,
                "used_contacts": contacts,
                "completed_pilots": completed}

    @extend_schema_field(RunFailureSerializer(allow_null=True))
    def get_error(self, run):
        if run.status != CampaignRun.Status.FAILED:
            return None
        messages = {
            "queue_unavailable": "Не удалось поставить расчёт в очередь. Попробуйте позже.",
            "engine_unavailable": "Агент расчёта сейчас недоступен.",
            "timeout": "Расчёт превысил допустимое время.",
            "execution_failed": "Расчёт завершился с ошибкой. Попробуйте ещё раз.",
        }
        code = run.error_code if run.error_code in messages else "execution_failed"
        return {"code": code, "message": messages[code]}

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
    explanation = serializers.CharField(allow_null=True)
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
