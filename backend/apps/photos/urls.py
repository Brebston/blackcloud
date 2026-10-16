from django.urls import path

from . import views

urlpatterns = [
    path("", views.TimelineView.as_view()),
    path("albums/", views.AlbumsView.as_view()),
    path("albums/<uuid:pk>/", views.AlbumDetailView.as_view()),
    path("albums/<uuid:pk>/items/", views.AlbumItemsView.as_view()),
    path("<uuid:pk>/", views.PhotoDetailView.as_view()),
]
