from django.conf import settings
from django.http import JsonResponse
from django.urls import include, path


def healthz(request):
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", healthz),
    path("api/", include("workspace.urls")),
    path("api/", include("assistant.urls")),
]

if settings.MOCKS_ENABLED:
    urlpatterns.append(path("mock/", include("mocks.urls")))
