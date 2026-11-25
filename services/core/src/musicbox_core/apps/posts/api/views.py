from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.pagination import parse_limit
from musicbox_core.apps.platform.authentication import principal_of, require_principal
from musicbox_core.apps.posts import selectors, services
from musicbox_core.apps.posts.api.serializers import (
    PostCreateSerializer,
    PostEditSerializer,
    PostPageSerializer,
    PostSerializer,
)
from musicbox_core.apps.users.selectors import visible_profile


class PostsView(APIView):
    @extend_schema(
        request=PostCreateSerializer,
        responses={201: PostSerializer},
        operation_id="posts_create",
        summary="Publish a post, optionally about one album",
        tags=["posts"],
    )
    def post(self, request: Request) -> Response:
        principal = require_principal(request)
        body = PostCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        post = services.create_post(
            principal, body.validated_data["body"], body.validated_data.get("album_id")
        )
        return Response(
            PostSerializer(selectors.post_payload(post, principal)).data,
            status=status.HTTP_201_CREATED,
        )


class PostView(APIView):
    @extend_schema(
        responses=PostSerializer,
        operation_id="posts_retrieve",
        summary="One post",
        tags=["posts"],
    )
    def get(self, request: Request, post_id: str) -> Response:
        viewer = principal_of(request)
        post = selectors.get_post(post_id, viewer)
        return Response(PostSerializer(selectors.post_payload(post, viewer)).data)

    @extend_schema(
        request=PostEditSerializer,
        responses=PostSerializer,
        operation_id="posts_partial_update",
        summary="Edit my post",
        tags=["posts"],
    )
    def patch(self, request: Request, post_id: str) -> Response:
        principal = require_principal(request)
        body = PostEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        post = services.edit_post(principal, post_id, body.validated_data)
        return Response(PostSerializer(selectors.post_payload(post, principal)).data)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="posts_destroy",
        summary="Delete my post with its comments and likes",
        tags=["posts"],
    )
    def delete(self, request: Request, post_id: str) -> Response:
        services.delete_post(require_principal(request), post_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserPostsView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
            OpenApiParameter("cursor", OpenApiTypes.STR),
        ],
        responses=PostPageSerializer,
        operation_id="user_posts_list",
        summary="A user's posts (newest first)",
        tags=["posts"],
    )
    def get(self, request: Request, username: str) -> Response:
        viewer = principal_of(request)
        page = selectors.posts_of(
            visible_profile(username, viewer),
            viewer,
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        payload = {
            "items": selectors.post_payloads(page.items, viewer),
            "next_cursor": page.next_cursor,
        }
        return Response(PostPageSerializer(payload).data)
