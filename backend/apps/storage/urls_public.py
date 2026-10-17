from django.urls import path

from . import views, views_public

# Токен посилання ніколи не передається в шляху URL (не потрапляє в журнали):
# info/authorize — POST з токеном у тілі, dl/<квиток> — одноразовий квиток на 60 с.
urlpatterns = [
    path("info/", views.PublicLinkInfoView.as_view()),
    path("authorize/", views.PublicLinkAuthorizeView.as_view()),
    path("dl/<str:ticket>/", views.PublicLinkDownloadView.as_view()),
    # Тека за посиланням (пропуск grant — у тілі POST, не в URL)
    path("folder/list/", views_public.FolderListView.as_view()),
    path("folder/image/", views_public.FolderImageView.as_view()),
    path("folder/file/", views_public.FolderFileTicketView.as_view()),
    path("folder/zip/", views_public.FolderZipTicketView.as_view()),
    path("zip/<str:ticket>/", views_public.FolderZipView.as_view()),
    # Скринька для файлів
    path("drop/start/", views_public.DropStartView.as_view()),
    path("drop/chunk/<int:index>/", views_public.DropChunkView.as_view()),
    path("drop/complete/", views_public.DropCompleteView.as_view()),
]
