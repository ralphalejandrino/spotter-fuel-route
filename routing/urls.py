from django.urls import path

from routing import views

urlpatterns = [
    path("api/v1/route/", views.route_view, name="route"),
    path("api/v1/health/", views.health_view, name="health"),
    path("map/", views.map_view, name="map"),
]
