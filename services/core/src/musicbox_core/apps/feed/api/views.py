from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.auth import Principal
from musicbox_common.pagination import parse_limit
from musicbox_core.apps.feed import selectors
from musicbox_core.apps.feed.api.serializers import FeedPageSerializer
from musicbox_core.apps.platform.authentication import principal_of, require_principal

PAGE_PARAMETERS = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
    OpenApiParameter("cursor", OpenApiTypes.STR),
]


class HomeFeedView(APIView):
    @extend_schema(
        parameters=PAGE_PARAMETERS,
        responses=FeedPageSerializer,
        operation_id="feed_home",
        summary="Activity of the users I follow and my own, newest first",
        tags=["feed"],
    )
    def get(self, request: Request) -> Response:
        viewer = require_principal(request)
        page = selectors.home_feed(
            viewer,
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        return _response(page, viewer)


class ExploreFeedView(APIView):
    @extend_schema(
        parameters=[
            *PAGE_PARAMETERS,
            OpenApiParameter("type", OpenApiTypes.STR, enum=["review", "post", "diary", "rating"]),
        ],
        responses=FeedPageSerializer,
        operation_id="feed_explore",
        summary="Recent public activity of everyone, newest first",
        tags=["feed"],
    )
    def get(self, request: Request) -> Response:
        viewer = principal_of(request)
        page = selectors.explore_feed(
            verb=selectors.parse_type(request.query_params.get("type")),
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        return _response(page, viewer)


def _response(page: selectors.FeedPage, viewer: Principal | None) -> Response:
    payload = {
        "items": selectors.feed_payloads(page.items, viewer),
        "next_cursor": page.next_cursor,
    }
    return Response(FeedPageSerializer(payload).data)
