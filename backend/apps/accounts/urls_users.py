from django.urls import path

from . import avatars, views

urlpatterns = [
    path("search/", views.UserSearchView.as_view()),
    path("avatars/", avatars.AvatarLookupView.as_view()),
    path("<uuid:pk>/avatar/", avatars.UserAvatarView.as_view()),
]
