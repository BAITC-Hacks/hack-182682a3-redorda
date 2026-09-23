from django.contrib import admin

from .models import CampaignResult, CampaignRun, Dataset, Pilot, RunEvent, RunResult

admin.site.register(Dataset)
admin.site.register(CampaignRun)
admin.site.register(RunEvent)
admin.site.register(Pilot)
admin.site.register(CampaignResult)
admin.site.register(RunResult)
