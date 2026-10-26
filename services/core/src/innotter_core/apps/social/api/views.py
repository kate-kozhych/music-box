from __future__ import annotations

from typing import Literal

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from innotter_common.pagination import parse_limit
from innotter_core.apps.platform.authentication import principal_of, require_principal
from innotter_core.apps.platform.permissions import IsAuthenticatedPrincipal
from innotter_core.apps.social import services
from innotter_core.apps.social.api.serializers import FollowPageSerializer
from innotter_core.apps.social.selectors import follow_page
from innotter_core.apps.users.selectors import visible_profile

PAGE_PARAMS = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
    OpenApiParameter("cursor", OpenApiTypes.STR, description="next_cursor from the previous page"),
]


class FollowView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]

    @extend_schema(
        request=None,
        responses={204: None},
        summary="Follow a user (idempotent)",
        tags=["social"],
    )
    def put(self, request: Request, username: str) -> Response:
        services.follow(require_principal(request), username)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @extend_schema(
        request=None,
        responses={204: None},
        summary="Unfollow a user (idempotent)",
        tags=["social"],
    )
    def delete(self, request: Request, username: str) -> Response:
        services.unfollow(require_principal(request), username)
        return Response(status=status.HTTP_204_NO_CONTENT)


class _FollowListView(APIView):
    direction: Literal["followers", "following"] = "followers"

    def get(self, request: Request, username: str) -> Response:
        viewer = principal_of(request)
        user = visible_profile(username, viewer)
        page = follow_page(
            user,
            self.direction,
            viewer,
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        payload = {
            "items": [
                {
                    "user": {
                        "user_id": e.user.user_id,
                        "username": e.user.username,
                        "display_name": e.user.display_name,
                        "avatar_url": e.user.avatar_url,
                    },
                    "followed_at": e.followed_at,
                    "viewer_follows": e.viewer_follows,
                }
                for e in page.items
            ],
            "next_cursor": page.next_cursor,
        }
        return Response(FollowPageSerializer(payload).data)


class FollowersView(_FollowListView):
    direction = "followers"

    @extend_schema(
        parameters=PAGE_PARAMS,
        responses=FollowPageSerializer,
        summary="Who follows this user (newest first)",
        tags=["social"],
    )
    def get(self, request: Request, username: str) -> Response:
        return super().get(request, username)


class FollowingView(_FollowListView):
    direction = "following"

    @extend_schema(
        parameters=PAGE_PARAMS,
        responses=FollowPageSerializer,
        summary="Whom this user follows (newest first)",
        tags=["social"],
    )
    def get(self, request: Request, username: str) -> Response:
        return super().get(request, username)
