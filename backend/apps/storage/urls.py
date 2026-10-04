from django.urls import path, re_path

from . import views

urlpatterns = [
    path("browse/", views.BrowseView.as_view()),
    path("search/", views.SearchView.as_view()),
    path("usage/", views.UsageView.as_view()),
    path("folders/", views.FolderCreateView.as_view()),
    path("folders/<uuid:pk>/", views.FolderDetailView.as_view()),
    path("items/<uuid:pk>/", views.FileDetailView.as_view()),
    path("items/<uuid:pk>/download/", views.FileDownloadView.as_view()),
    path("uploads/", views.UploadStartView.as_view()),
    path("uploads/<uuid:pk>/", views.UploadDetailView.as_view()),
    path("uploads/<uuid:pk>/chunks/<int:index>/", views.UploadChunkView.as_view()),
    path("uploads/<uuid:pk>/complete/", views.UploadCompleteView.as_view()),
    path("trash/", views.TrashView.as_view()),
    re_path(r"^trash/(?P<op>restore|purge|empty)/$", views.TrashActionView.as_view()),
    path("shares/", views.SharesView.as_view()),
    path("shares/<uuid:pk>/", views.ShareDetailView.as_view()),
    path("shared-with-me/", views.SharedWithMeView.as_view()),
    path("links/", views.PublicLinksView.as_view()),
    path("links/<uuid:pk>/", views.PublicLinkDetailView.as_view()),
]
