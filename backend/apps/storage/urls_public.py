from django.urls import path

from . import views

# Токен посилання ніколи не передається в шляху URL (не потрапляє в журнали):
# info/authorize — POST з токеном у тілі, dl/<квиток> — одноразовий квиток на 60 с.
urlpatterns = [
    path("info/", views.PublicLinkInfoView.as_view()),
    path("authorize/", views.PublicLinkAuthorizeView.as_view()),
    path("dl/<str:ticket>/", views.PublicLinkDownloadView.as_view()),
]
