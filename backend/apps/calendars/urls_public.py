from django.urls import re_path

from .views import PublicFeedView

urlpatterns = [re_path(r"^(?P<token>[A-Za-z0-9_-]{20,100})\.ics$", PublicFeedView.as_view())]
