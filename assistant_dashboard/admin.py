from django.contrib import admin

from .models import BetaLocation, ImportRun, ProductionObservation


@admin.register(BetaLocation)
class BetaLocationAdmin(admin.ModelAdmin):
    list_display = ("name", "domain_key", "latitude", "longitude", "beta_total")
    search_fields = ("name", "domain_key")


@admin.register(ProductionObservation)
class ProductionObservationAdmin(admin.ModelAdmin):
    list_display = ("date", "domain", "category", "cumulative_users", "cumulative_messages", "location")
    list_filter = ("category", "date")
    search_fields = ("domain",)


@admin.register(ImportRun)
class ImportRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at", "status", "number_of_records_fetched",
        "number_of_records_created", "number_of_records_updated",
    )
    readonly_fields = [f.name for f in ImportRun._meta.fields]
