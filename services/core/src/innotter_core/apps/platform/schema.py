from typing import Any

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class JwtAuthenticationScheme(OpenApiAuthenticationExtension):  # type: ignore[no-untyped-call]
    target_class = "innotter_core.apps.platform.authentication.JwtAuthentication"
    name = "bearerAuth"

    def get_security_definition(self, auto_schema: Any) -> dict[str, str]:
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
