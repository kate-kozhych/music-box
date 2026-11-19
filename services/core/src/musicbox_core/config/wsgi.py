import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "musicbox_core.config.settings.base")

application = get_wsgi_application()
