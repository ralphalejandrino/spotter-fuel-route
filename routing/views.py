from django.conf import settings
from django.shortcuts import render
from rest_framework import serializers, status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from routing.corridor import get_index
from routing.geocode import GeocodeError
from routing.providers import RoutingError
from routing.service import plan_route
from routing.solver import InfeasibleRoute


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(max_length=200)
    finish = serializers.CharField(max_length=200)
    mpg = serializers.FloatField(required=False, min_value=0.1, max_value=200)
    range_miles = serializers.FloatField(required=False, min_value=1, max_value=5000)
    corridor_miles = serializers.FloatField(required=False, min_value=0.5, max_value=100)


@api_view(["GET", "POST"])
def route_view(request):
    """Plan a fuel-optimal route between two US locations.

    GET is supported alongside POST purely so the whole thing is demonstrable from a
    single pasteable URL.
    """
    data = request.data if request.method == "POST" else request.query_params
    form = RouteRequestSerializer(data=data)
    form.is_valid(raise_exception=True)
    v = form.validated_data

    try:
        body = plan_route(
            v["start"],
            v["finish"],
            mpg=v.get("mpg"),
            range_miles=v.get("range_miles"),
            corridor_miles=v.get("corridor_miles"),
        )
    except GeocodeError as exc:
        return Response({"error": "geocode", "detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except InfeasibleRoute as exc:
        return Response(
            {
                "error": "infeasible_route",
                "detail": str(exc),
                "gap_miles": round(exc.gap_miles, 1),
                "after_mile": round(exc.after_mile, 1),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )
    except RoutingError as exc:
        return Response(
            {"error": "routing_provider", "detail": str(exc)},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    return Response(body)


@api_view(["GET"])
def health_view(request):
    try:
        n = len(get_index())
    except RuntimeError as exc:
        return Response({"status": "degraded", "detail": str(exc)}, status=503)
    return Response({"status": "ok", "stations_loaded": n, "provider": settings.OSRM_BASE_URL})


def map_view(request):
    """Human-readable map of the planned route. The brief asks for a map, not only JSON."""
    return render(
        request,
        "routing/map.html",
        {
            "start": request.GET.get("start", "Los Angeles, CA"),
            "finish": request.GET.get("finish", "New York, NY"),
        },
    )
