from django.urls import path

from . import views

app_name = "assistant_dashboard"

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("api/dashboard/summary/", views.api_summary, name="api_summary"),
    path("api/dashboard/timeseries/", views.api_timeseries, name="api_timeseries"),
    path("api/dashboard/locations/", views.api_locations, name="api_locations"),
    path("api/dashboard/location/<int:pk>/", views.api_location, name="api_location"),
    path("api/dashboard/shapes/", views.api_shapes, name="api_shapes"),
]
