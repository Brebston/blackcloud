from django.urls import path

from . import api

urlpatterns = [
    path("passwords/", api.PasswordsView.as_view()),
    path("passwords/<uuid:pk>/", api.PasswordDetailView.as_view()),
]
