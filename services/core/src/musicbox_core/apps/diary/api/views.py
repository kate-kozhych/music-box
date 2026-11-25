from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.pagination import parse_limit
from musicbox_core.apps.diary import selectors, services
from musicbox_core.apps.diary.api.serializers import (
    DiaryCreateSerializer,
    DiaryEditSerializer,
    DiaryEntrySerializer,
    DiaryPageSerializer,
)
from musicbox_core.apps.platform.authentication import principal_of, require_principal
from musicbox_core.apps.users.selectors import visible_profile


class DiaryEntriesView(APIView):
    @extend_schema(
        request=DiaryCreateSerializer,
        responses={201: DiaryEntrySerializer},
        operation_id="diary_entries_create",
        summary="Log a listen in my diary",
        tags=["diary"],
    )
    def post(self, request: Request) -> Response:
        principal = require_principal(request)
        body = DiaryCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        entry = services.log_listen(principal, body.validated_data)
        return Response(
            DiaryEntrySerializer(selectors.entry_payload(entry)).data,
            status=status.HTTP_201_CREATED,
        )


class DiaryEntryView(APIView):
    @extend_schema(
        responses=DiaryEntrySerializer,
        operation_id="diary_entries_retrieve",
        summary="One diary entry",
        tags=["diary"],
    )
    def get(self, request: Request, entry_id: str) -> Response:
        entry = selectors.get_entry(entry_id, principal_of(request))
        return Response(DiaryEntrySerializer(selectors.entry_payload(entry)).data)

    @extend_schema(
        request=DiaryEditSerializer,
        responses=DiaryEntrySerializer,
        operation_id="diary_entries_partial_update",
        summary="Edit my diary entry (never changes my album rating)",
        tags=["diary"],
    )
    def patch(self, request: Request, entry_id: str) -> Response:
        principal = require_principal(request)
        body = DiaryEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        entry = services.edit_entry(principal, entry_id, body.validated_data)
        return Response(DiaryEntrySerializer(selectors.entry_payload(entry)).data)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="diary_entries_destroy",
        summary="Delete my diary entry",
        tags=["diary"],
    )
    def delete(self, request: Request, entry_id: str) -> Response:
        services.delete_entry(require_principal(request), entry_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserDiaryView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 30"),
            OpenApiParameter("cursor", OpenApiTypes.STR),
            OpenApiParameter("year", OpenApiTypes.INT),
        ],
        responses=DiaryPageSerializer,
        operation_id="user_diary_list",
        summary="A user's diary (latest listens first; private entries only for the owner)",
        tags=["diary"],
    )
    def get(self, request: Request, username: str) -> Response:
        viewer = principal_of(request)
        page = selectors.diary_of(
            visible_profile(username, viewer),
            viewer,
            year=selectors.parse_year(request.query_params.get("year")),
            limit=parse_limit(request.query_params.get("limit"), selectors.DEFAULT_LIMIT),
            cursor=request.query_params.get("cursor") or None,
        )
        payload = {
            "items": [selectors.entry_payload(entry, excerpt=True) for entry in page.items],
            "next_cursor": page.next_cursor,
        }
        return Response(DiaryPageSerializer(payload).data)
