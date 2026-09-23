from django.contrib import admin

from .models import CampaignRun, Dataset

admin.site.register(Dataset)
admin.site.register(CampaignRun)
