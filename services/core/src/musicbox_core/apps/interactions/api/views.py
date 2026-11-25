from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.pagination import parse_limit
from musicbox_core.apps.interactions import likes, selectors, services
from musicbox_core.apps.interactions.api.serializers import (
    CommentInputSerializer,
    CommentPageSerializer,
    CommentSerializer,
)
from musicbox_core.apps.platform.authentication import principal_of, require_principal
from musicbox_core.apps.platform.content import ContentType

PAGE_PARAMS = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
    OpenApiParameter("cursor", OpenApiTypes.STR),
]


def _list(request: Request, target_type: ContentType, target_id: str) -> Response:
    viewer = principal_of(request)
    page = selectors.comments_on(
        target_type,
        target_id,
        viewer,
        limit=parse_limit(request.query_params.get("limit")),
        cursor=request.query_params.get("cursor") or None,
    )
    payload = {
        "items": selectors.comment_payloads(page.items, viewer),
        "next_cursor": page.next_cursor,
    }
    return Response(CommentPageSerializer(payload).data)


def _create(request: Request, target_type: ContentType, target_id: str) -> Response:
    principal = require_principal(request)
    body = CommentInputSerializer(data=request.data)
    body.is_valid(raise_exception=True)
    comment = services.create_comment(
        principal, target_type, target_id, body.validated_data["body"]
    )
    return Response(
        CommentSerializer(selectors.comment_payload(comment, principal)).data,
        status=status.HTTP_201_CREATED,
    )


def _like(request: Request, target_type: ContentType, target_id: str) -> Response:
    likes.like(require_principal(request), target_type, target_id)
    return Response(status=status.HTTP_204_NO_CONTENT)


def _unlike(request: Request, target_type: ContentType, target_id: str) -> Response:
    likes.unlike(require_principal(request), target_type, target_id)
    return Response(status=status.HTTP_204_NO_CONTENT)


class PostCommentsView(APIView):
    @extend_schema(
        parameters=PAGE_PARAMS,
        responses=CommentPageSerializer,
        operation_id="post_comments_list",
        summary="Comments on a post (oldest first)",
        tags=["comments"],
    )
    def get(self, request: Request, target_id: str) -> Response:
        return _list(request, ContentType.POST, target_id)

    @extend_schema(
        request=CommentInputSerializer,
        responses={201: CommentSerializer},
        operation_id="post_comments_create",
        summary="Comment on a post",
        tags=["comments"],
    )
    def post(self, request: Request, target_id: str) -> Response:
        return _create(request, ContentType.POST, target_id)


class ReviewCommentsView(APIView):
    @extend_schema(
        parameters=PAGE_PARAMS,
        responses=CommentPageSerializer,
        operation_id="review_comments_list",
        summary="Comments on a review (oldest first)",
        tags=["comments"],
    )
    def get(self, request: Request, target_id: str) -> Response:
        return _list(request, ContentType.REVIEW, target_id)

    @extend_schema(
        request=CommentInputSerializer,
        responses={201: CommentSerializer},
        operation_id="review_comments_create",
        summary="Comment on a review",
        tags=["comments"],
    )
    def post(self, request: Request, target_id: str) -> Response:
        return _create(request, ContentType.REVIEW, target_id)


class CommentView(APIView):
    @extend_schema(
        request=CommentInputSerializer,
        responses=CommentSerializer,
        operation_id="comments_partial_update",
        summary="Edit my comment",
        tags=["comments"],
    )
    def patch(self, request: Request, comment_id: str) -> Response:
        principal = require_principal(request)
        body = CommentInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        comment = services.edit_comment(principal, comment_id, body.validated_data["body"])
        return Response(CommentSerializer(selectors.comment_payload(comment, principal)).data)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="comments_destroy",
        summary="Delete my comment",
        tags=["comments"],
    )
    def delete(self, request: Request, comment_id: str) -> Response:
        services.delete_comment(require_principal(request), comment_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class PostLikeView(APIView):
    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="post_like_create",
        summary="Like a post (idempotent)",
        tags=["likes"],
    )
    def put(self, request: Request, target_id: str) -> Response:
        return _like(request, ContentType.POST, target_id)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="post_like_destroy",
        summary="Unlike a post (idempotent)",
        tags=["likes"],
    )
    def delete(self, request: Request, target_id: str) -> Response:
        return _unlike(request, ContentType.POST, target_id)


class ReviewLikeView(APIView):
    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="review_like_create",
        summary="Like a review (idempotent)",
        tags=["likes"],
    )
    def put(self, request: Request, target_id: str) -> Response:
        return _like(request, ContentType.REVIEW, target_id)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="review_like_destroy",
        summary="Unlike a review (idempotent)",
        tags=["likes"],
    )
    def delete(self, request: Request, target_id: str) -> Response:
        return _unlike(request, ContentType.REVIEW, target_id)


class CommentLikeView(APIView):
    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="comment_like_create",
        summary="Like a comment (idempotent)",
        tags=["likes"],
    )
    def put(self, request: Request, target_id: str) -> Response:
        return _like(request, ContentType.COMMENT, target_id)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="comment_like_destroy",
        summary="Unlike a comment (idempotent)",
        tags=["likes"],
    )
    def delete(self, request: Request, target_id: str) -> Response:
        return _unlike(request, ContentType.COMMENT, target_id)
