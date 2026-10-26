from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from innotter_core.apps.platform.authentication import principal_of
from innotter_core.apps.profiles.api.serializers import PublicProfileSerializer
from innotter_core.apps.profiles.selectors import public_profile


class PublicProfileView(APIView):
    @extend_schema(
        responses=PublicProfileSerializer,
        summary="Public profile (guest-readable; blocked users are 404 except for Admins)",
        tags=["profiles"],
    )
    def get(self, request: Request, username: str) -> Response:
        viewer = principal_of(request)
        profile = public_profile(username, viewer)
        user = profile.user
        data = {
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
            "bio": user.bio,
            "avatar_url": user.avatar_url,
            "joined_at": user.joined_at,
            "counts": {
                "ratings": 0,
                "reviews": 0,
                "diary_entries": 0,
                "posts": 0,
                "followers": profile.followers,
                "following": profile.following,
            },
            "favourites": {"albums": [], "artists": []},
            "viewer": {"is_self": profile.is_self, "follows": profile.viewer_follows},
        }
        if viewer is not None and viewer.is_admin:
            data["status"] = user.status
        return Response(PublicProfileSerializer(data).data)
