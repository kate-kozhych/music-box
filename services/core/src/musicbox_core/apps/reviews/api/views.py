from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.auth import Principal
from musicbox_common.pagination import parse_limit
from musicbox_core.apps.catalog.selectors import get_album
from musicbox_core.apps.platform.authentication import principal_of, require_principal
from musicbox_core.apps.reviews import selectors, services
from musicbox_core.apps.reviews.api.serializers import (
    ReviewCreateSerializer,
    ReviewEditSerializer,
    ReviewPageSerializer,
    ReviewSerializer,
)
from musicbox_core.apps.users.selectors import visible_profile

PAGE_PARAMS = [
    OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
    OpenApiParameter("cursor", OpenApiTypes.STR),
]


def _page_response(page: selectors.ReviewPage, viewer: Principal | None) -> Response:
    payload = {
        "items": [selectors.review_payload(review, viewer, excerpt=True) for review in page.items],
        "next_cursor": page.next_cursor,
    }
    return Response(ReviewPageSerializer(payload).data)


class AlbumReviewsView(APIView):
    @extend_schema(
        parameters=[
            *PAGE_PARAMS,
            OpenApiParameter("sort", OpenApiTypes.STR, enum=list(selectors.SORTS)),
        ],
        responses=ReviewPageSerializer,
        operation_id="album_reviews_list",
        summary="Community reviews of an album",
        tags=["reviews"],
    )
    def get(self, request: Request, album_id: str) -> Response:
        viewer = principal_of(request)
        page = selectors.album_reviews(
            get_album(album_id),
            viewer,
            sort=selectors.parse_sort(request.query_params.get("sort")),
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        return _page_response(page, viewer)

    @extend_schema(
        request=ReviewCreateSerializer,
        responses={201: ReviewSerializer},
        operation_id="album_reviews_create",
        summary="Write my review of an album",
        tags=["reviews"],
    )
    def post(self, request: Request, album_id: str) -> Response:
        principal = require_principal(request)
        body = ReviewCreateSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        review = services.create_review(
            principal, album_id, body.validated_data["body"], body.validated_data.get("rating")
        )
        payload = selectors.review_payload(review, principal)
        return Response(ReviewSerializer(payload).data, status=status.HTTP_201_CREATED)


class ReviewView(APIView):
    @extend_schema(
        responses=ReviewSerializer,
        operation_id="reviews_retrieve",
        summary="One review",
        tags=["reviews"],
    )
    def get(self, request: Request, review_id: str) -> Response:
        viewer = principal_of(request)
        review = selectors.get_review(review_id, viewer)
        return Response(ReviewSerializer(selectors.review_payload(review, viewer)).data)

    @extend_schema(
        request=ReviewEditSerializer,
        responses=ReviewSerializer,
        operation_id="reviews_partial_update",
        summary="Edit my review",
        tags=["reviews"],
    )
    def patch(self, request: Request, review_id: str) -> Response:
        principal = require_principal(request)
        body = ReviewEditSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        review = services.edit_review(principal, review_id, body.validated_data["body"])
        return Response(ReviewSerializer(selectors.review_payload(review, principal)).data)

    @extend_schema(
        request=None,
        responses={204: None},
        operation_id="reviews_destroy",
        summary="Delete my review",
        tags=["reviews"],
    )
    def delete(self, request: Request, review_id: str) -> Response:
        services.delete_review(require_principal(request), review_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserReviewsView(APIView):
    @extend_schema(
        parameters=PAGE_PARAMS,
        responses=ReviewPageSerializer,
        operation_id="user_reviews_list",
        summary="A user's reviews (newest first)",
        tags=["reviews"],
    )
    def get(self, request: Request, username: str) -> Response:
        viewer = principal_of(request)
        page = selectors.reviews_of(
            visible_profile(username, viewer),
            viewer,
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        return _page_response(page, viewer)
