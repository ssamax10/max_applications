from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel

from app.core.auth import TenantContext, tenant_context_dependency
from app.core.settings import settings
from app.telemetry.metrics import metrics_content_type, metrics_payload

router = APIRouter()

TOOLS = [
    {
        'name': 'balloon.create',
        'description': 'Create balloon annotation in tenant drawing',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'tenant_id': {'type': 'string'},
                'drawing_id': {'type': 'string'},
                'label': {'type': 'string'},
                'geometry': {'type': 'object'},
            },
            'required': ['tenant_id', 'drawing_id', 'label', 'geometry'],
        },
    },
    {
        'name': 'revision.diff',
        'description': 'Compute revision diff for drawing',
        'inputSchema': {
            'type': 'object',
            'properties': {
                'tenant_id': {'type': 'string'},
                'drawing_id': {'type': 'string'},
                'left_revision': {'type': 'string'},
                'right_revision': {'type': 'string'},
            },
            'required': ['tenant_id', 'drawing_id', 'left_revision', 'right_revision'],
        },
    },
]


class McpInvokeRequest(BaseModel):
    tool: str
    arguments: dict[str, Any]


class McpInvokeResponse(BaseModel):
    tenant_id: str
    tool: str
    invoked_at: str
    result: dict[str, Any]


@router.get('/health/live')
def health_live() -> dict[str, str]:
    return {'status': 'alive', 'service': settings.service_name}


@router.get('/health/ready')
def health_ready() -> dict[str, str]:
    return {'status': 'ready', 'service': settings.service_name}


@router.get('/health')
def health() -> dict[str, str]:
    return {'status': 'ok', 'service': settings.service_name}


@router.get('/tenant-context')
def tenant_context(context: TenantContext = Depends(tenant_context_dependency)) -> dict[str, str | None]:
    return {'tenant_id': context.tenant_id, 'subject': context.subject, 'service': settings.service_name}


@router.get('/mcp/tools')
def list_tools() -> dict[str, object]:
    return {'tools': TOOLS}


@router.post('/mcp/invoke', response_model=McpInvokeResponse)
def invoke_tool(
    request: McpInvokeRequest,
    context: TenantContext = Depends(tenant_context_dependency),
) -> McpInvokeResponse:
    known_tools = {tool['name'] for tool in TOOLS}
    if request.tool not in known_tools:
        raise HTTPException(status_code=404, detail='Tool not found')

    result = {
        'accepted': True,
        'arguments': request.arguments,
        'note': 'Execution is currently mocked for local development.',
    }
    return McpInvokeResponse(
        tenant_id=context.tenant_id,
        tool=request.tool,
        invoked_at=datetime.now(timezone.utc).isoformat(),
        result=result,
    )


@router.get('/metrics', include_in_schema=False)
def metrics() -> Response:
    return Response(content=metrics_payload(), media_type=metrics_content_type())
