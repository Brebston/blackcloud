from django.urls import re_path

from .views import dav_view

# /dav, /dav/ і /dav/<будь-який шлях> (шлях уже декодовано; перевірки — у paths.py)
urlpatterns = [
    re_path(r"^dav/?$", dav_view),
    re_path(r"^dav/(?P<path>.+)$", dav_view),
]
