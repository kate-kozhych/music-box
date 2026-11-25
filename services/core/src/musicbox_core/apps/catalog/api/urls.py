from django.urls import path

from musicbox_core.apps.catalog.api.views import (
    AlbumByMbidView,
    AlbumDetailView,
    AlbumSearchView,
    DiscoverAlbumsView,
)

urlpatterns = [
    path("albums", AlbumSearchView.as_view(), name="album-search"),
    path("discover/albums", DiscoverAlbumsView.as_view(), name="discover-albums"),
    path("albums/mbid/<str:mbid>", AlbumByMbidView.as_view(), name="album-by-mbid"),
    path("albums/<str:album_id>", AlbumDetailView.as_view(), name="album-detail"),
]
