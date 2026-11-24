from __future__ import annotations

import uuid
from typing import Any

from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from musicbox_common.errors import (
    AppError,
    ErrorCode,
    FieldError,
    catalog_unavailable,
    not_found,
    validation_failed,
)
from musicbox_core.apps.catalog import attribution, search, selectors, services, tasks
from musicbox_core.apps.catalog.api.serializers import (
    AlbumDetailSerializer,
    AlbumSearchPageSerializer,
)
from musicbox_core.apps.catalog.coverart import SIZE_250, front_url
from musicbox_core.apps.catalog.mapping import is_mbid
from musicbox_core.apps.catalog.models import Album
from musicbox_core.apps.catalog.ports import (
    MetadataUnavailableError,
    ReleaseGroupNotFoundError,
    UnsupportedReleaseTypeError,
)
from musicbox_core.apps.catalog.provider import get_provider, is_live
from musicbox_core.apps.platform.authentication import principal_of


class AlbumSearchView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("q", OpenApiTypes.STR, required=True, description="2-100 characters"),
            OpenApiParameter("page", OpenApiTypes.INT, description="1-based, default 1"),
            OpenApiParameter("size", OpenApiTypes.INT, description="1-50, default 20"),
        ],
        responses=AlbumSearchPageSerializer,
        operation_id="albums_search",
        summary="Search albums (local catalog, merged with MusicBrainz in live mode)",
        tags=["catalog"],
    )
    def get(self, request: Request) -> Response:
        result = search.search_albums(
            request.query_params.get("q"),
            page=search.parse_page(request.query_params.get("page")),
            size=search.parse_size(request.query_params.get("size")),
        )
        payload = {
            "items": [
                {
                    "id": str(hit.album.id) if hit.album else None,
                    "mbid": hit.mbid,
                    "title": hit.title,
                    "artist_credit": hit.artist_credit,
                    "year": hit.year,
                    "type": hit.primary_type,
                    "cover_url": _hit_cover_url(hit),
                    "imported": hit.imported,
                }
                for hit in result.items
            ],
            "page": result.page,
            "size": result.size,
            "total": result.total,
            "external_status": result.external_status,
            "attribution": attribution.notice(),
        }
        return Response(AlbumSearchPageSerializer(payload).data)


class AlbumDetailView(APIView):
    @extend_schema(
        responses=AlbumDetailSerializer,
        operation_id="albums_retrieve",
        summary="Album page by local id",
        tags=["catalog"],
    )
    def get(self, request: Request, album_id: str) -> Response:
        try:
            parsed = uuid.UUID(album_id)
        except ValueError as exc:
            raise not_found("No such album.") from exc
        album = selectors.album_detail_queryset().filter(id=parsed).first()
        if album is None:
            raise AppError(404, ErrorCode.UNKNOWN_ALBUM, "No such album.")
        tasks.refresh_if_stale(album)
        return Response(AlbumDetailSerializer(album_payload(album, request)).data)


class AlbumByMbidView(APIView):
    @extend_schema(
        responses=AlbumDetailSerializer,
        operation_id="albums_retrieve_by_mbid",
        summary="Album page by MusicBrainz id, imported on demand",
        tags=["catalog"],
    )
    def get(self, request: Request, mbid: str) -> Response:
        if not is_mbid(mbid):
            raise validation_failed([FieldError("mbid", "invalid", "That is not a valid MBID.")])
        normalised = mbid.lower()
        album = selectors.album_detail_queryset().filter(mbid=normalised).first()
        if album is not None:
            tasks.refresh_if_stale(album)
            return Response(AlbumDetailSerializer(album_payload(album, request)).data)

        album = _import_album(normalised)
        fresh = selectors.album_detail_queryset().get(pk=album.pk)
        return Response(AlbumDetailSerializer(album_payload(fresh, request)).data)


def _import_album(mbid: str) -> Album:
    try:
        return services.import_album(get_provider(), mbid)
    except UnsupportedReleaseTypeError as exc:
        raise AppError(
            422,
            ErrorCode.UNSUPPORTED_RELEASE_TYPE,
            "Only release groups of type Album or EP can be added.",
        ) from exc
    except ReleaseGroupNotFoundError as exc:
        raise AppError(404, ErrorCode.UNKNOWN_ALBUM, "No such album.") from exc
    except MetadataUnavailableError as exc:
        raise catalog_unavailable(exc.retry_after) from exc


def _hit_cover_url(hit: selectors.SearchHit) -> str | None:
    if not is_live():
        return None
    return hit.album.cover_url_250 if hit.album else front_url(hit.mbid, SIZE_250)


def album_payload(album: Album, request: Request) -> dict[str, Any]:
    release_date, precision = selectors.release_date_payload(album)
    return {
        "id": str(album.id),
        "mbid": album.mbid,
        "title": album.title,
        "artists": [
            {
                "id": str(credit.artist_id),
                "mbid": credit.artist.mbid,
                "name": credit.artist.name,
                "join_phrase": credit.join_phrase,
                "url": attribution.entity_url("artist", credit.artist.mbid),
            }
            for credit in album.credits.all()
        ],
        "artist_credit": album.artist_credit,
        "type": str(album.primary_type),
        "secondary_types": list(album.secondary_types or []),
        "release_date": release_date,
        "release_date_precision": precision,
        "genres": [
            {"name": tag.genre.name, "slug": tag.genre.slug}
            for tag in sorted(album.genre_tags.all(), key=lambda t: (-t.votes, t.genre.name))
        ],
        "cover_url": album.cover_url_250 if is_live() else None,
        "cover_url_500": album.cover_url_500 if is_live() else None,
        "stats": selectors.rating_stats(album),
        "recent_reviews": [],
        "viewer": selectors.viewer_state(album, principal_of(request)),
        "source": attribution.album_source(album.mbid),
        "attribution": attribution.notice(),
    }
