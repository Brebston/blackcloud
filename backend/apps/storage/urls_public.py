from django.urls import path

from . import views

urlpatterns = [
    path("<str:token>/", views.PublicLinkInfoView.as_view()),
    path("<str:token>/authorize/", views.PublicLinkAuthorizeView.as_view()),
    path("<str:token>/download/", views.PublicLinkDownloadView.as_view()),
]
