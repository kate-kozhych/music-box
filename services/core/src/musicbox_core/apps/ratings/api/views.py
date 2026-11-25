from __future__ import annotations

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.pagination import parse_limit
from musicbox_core.apps.catalog.selectors import album_summary, rating_stats_payload
from musicbox_core.apps.platform.authentication import principal_of, require_principal
from musicbox_core.apps.platform.permissions import IsAuthenticatedPrincipal
from musicbox_core.apps.ratings import selectors, services
from musicbox_core.apps.ratings.api.serializers import (
    RatingInputSerializer,
    RatingPageSerializer,
    RatingResultSerializer,
)
from musicbox_core.apps.ratings.values import to_wire
from musicbox_core.apps.users.selectors import visible_profile


class AlbumRatingView(APIView):
    permission_classes = [IsAuthenticatedPrincipal]  # noqa: RUF012  # DRF view config

    @extend_schema(
        request=RatingInputSerializer,
        responses=RatingResultSerializer,
        summary="Set or change my rating of an album",
        tags=["ratings"],
    )
    def put(self, request: Request, album_id: str) -> Response:
        body = RatingInputSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        value, stats = services.set_rating(
            require_principal(request), album_id, body.validated_data["value"]
        )
        payload = {"value": to_wire(value), "album_stats": rating_stats_payload(stats)}
        return Response(RatingResultSerializer(payload).data)

    @extend_schema(
        request=None,
        responses={204: None},
        summary="Remove my rating of an album (idempotent)",
        tags=["ratings"],
    )
    def delete(self, request: Request, album_id: str) -> Response:
        services.remove_rating(require_principal(request), album_id)
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserRatingsView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, description="1-50, default 20"),
            OpenApiParameter("cursor", OpenApiTypes.STR),
        ],
        responses=RatingPageSerializer,
        summary="A user's ratings (newest first)",
        tags=["ratings"],
    )
    def get(self, request: Request, username: str) -> Response:
        page = selectors.ratings_of(
            visible_profile(username, principal_of(request)),
            limit=parse_limit(request.query_params.get("limit")),
            cursor=request.query_params.get("cursor") or None,
        )
        payload = {
            "items": [
                {
                    "album": album_summary(rating.album),
                    "value": to_wire(rating.value),
                    "created_at": rating.created_at,
                    "updated_at": rating.updated_at,
                }
                for rating in page.items
            ],
            "next_cursor": page.next_cursor,
        }
        return Response(RatingPageSerializer(payload).data)
