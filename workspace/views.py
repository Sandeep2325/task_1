import functools
import json
import logging

from django.conf import settings
from django.http import JsonResponse, StreamingHttpResponse

from . import auth, services
from .models import PlacedItem

log = logging.getLogger(__name__)


def api(*methods):
    """Authenticate, enforce the HTTP method, parse JSON and map service errors to responses."""

    def wrap(view):
        @functools.wraps(view)
        async def inner(request, *args, **kwargs):
            if request.method not in methods:
                return JsonResponse({"error": "method_not_allowed"}, status=405)
            try:
                request.principal = auth.principal_from_request(request)
            except auth.AuthError as e:
                return JsonResponse(
                    {"error": "unauthorized", "detail": str(e)}, status=401, headers={"WWW-Authenticate": "Bearer"}
                )
            if request.method == "POST":
                try:
                    request.json = json.loads(request.body or b"{}")
                except ValueError:
                    return JsonResponse({"error": "invalid_json"}, status=400)
            try:
                result = await view(request, *args, **kwargs)
            except services.ServiceError as e:
                return JsonResponse({"error": e.code, "detail": e.detail}, status=e.status)
            return result if not isinstance(result, dict) else JsonResponse(result)

        return inner

    return wrap


@api("GET")
async def project_layout(request, project_id):
    items = [services.item_summary(i) async for i in PlacedItem.objects.select_related("parent").filter(project_id=project_id)]
    if not items:
        raise services.NotFound(f"project {project_id} not found")
    return {"project_id": project_id, "items": items}


@api("GET")
async def sidebar(request, project_id, tag):
    return await services.sidebar(project_id, tag, request.principal)


@api("POST")
async def resize(request, project_id, tag):
    body = request.json
    return await services.resize(project_id, tag, body, request.principal, body.get("expected_revision"))


@api("GET")
async def project_bom(request, project_id):
    return await services.project_bom(project_id, request.principal)


@api("GET")
async def sizing_stream(request, project_id, tag):
    """Server-sent events: one `sizing` event when the slow run for this item lands.

    The panel opens this when the sidebar returned sizing with pending=true.
    """
    principal = request.principal
    await services.load_item(project_id, tag)  # 404 before we start streaming

    async def events():
        yield ": waiting for sizing\n\n"
        try:
            section = await services.sizing_when_ready(project_id, tag, principal, settings.RESIZE_TIMEOUT_S)
        except services.ServiceError as e:
            yield f"event: error\ndata: {json.dumps({'error': e.code, 'detail': e.detail})}\n\n"
            return
        yield f"event: sizing\ndata: {json.dumps(section)}\n\n"

    response = StreamingHttpResponse(events(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
