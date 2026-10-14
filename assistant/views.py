from workspace.views import api

from . import tools


@api("GET")
async def tool_list(request):
    return {"tools": tools.definitions()}


@api("POST")
async def tool_invoke(request, name):
    """Called by the LLM orchestrator on behalf of the signed-in user (it forwards the user's token)."""
    return await tools.invoke(name, request.json.get("arguments", {}), request.principal)


@api("POST")
async def confirm(request, action_id):
    """Called by the frontend when the user clicks Confirm. Not exposed as a tool."""
    return await tools.confirm(action_id, request.principal)
