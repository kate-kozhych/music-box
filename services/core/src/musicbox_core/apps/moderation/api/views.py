from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.pagination import parse_limit
from musicbox_core.apps.moderation import selectors, services
from musicbox_core.apps.moderation.api.serializers import HideSerializer, ModerationLogSerializer
from musicbox_core.apps.platform.authentication import require_principal
from musicbox_core.apps.platform.permissions import CanModerateContent

TARGET_PARAMS = [
    OpenApiParameter(
        "content_type", OpenApiTypes.STR, OpenApiParameter.PATH, enum=list(services.PATHS)
    ),
]


class HideView(APIView):
    permission_classes = [CanModerateContent]  # noqa: RUF012  # DRF view config

    @extend_schema(
        parameters=TARGET_PARAMS,
        request=HideSerializer,
        responses={204: None},
        operation_id="moderation_hide",
        summary="Hide content with a reason",
        tags=["moderation"],
    )
    def post(self, request: Request, content_type: str, content_id: str) -> Response:
        body = HideSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        services.hide(
            require_principal(request), content_type, content_id, body.validated_data["reason"]
        )
        return Response(status=status.HTTP_204_NO_CONTENT)


class RestoreView(APIView):
    permission_classes = [CanModerateContent]  # noqa: RUF012  # DRF view config

    @extend_schema(
        parameters=TARGET_PARAMS,
        request=None,
        responses={204: None},
        operation_id="moderation_restore",
        summary="Restore hidden content",
        tags=["moderation"],
    )
    def post(self, request: Request, content_type: str, content_id: str) -> Response:
        services.restore(require_principal(request), content_type, content_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class ModerationLogView(APIView):
    permission_classes = [CanModerateContent]  # noqa: RUF012  # DRF view config

    @extend_schema(
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
            OpenApiParameter("cursor", OpenApiTypes.STR),
        ],
        responses=ModerationLogSerializer,
        operation_id="moderation_log_list",
        summary="Content moderation log (newest first)",
        tags=["moderation"],
    )
    def get(self, request: Request) -> Response:
        page = selectors.moderation_log(
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        payload = {
            "items": selectors.log_payloads(page.items),
            "next_cursor": page.next_cursor,
        }
        return Response(ModerationLogSerializer(payload).data)
