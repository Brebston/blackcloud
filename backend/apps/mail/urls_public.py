from django.urls import path

from . import views

# Токен конфіденційного листа передається лише в тілі POST (не в URL, не в журналах)
urlpatterns = [
    path("open/", views.PublicConfidentialView.as_view()),
    path("r/<str:ticket>/", views.PublicConfidentialRenderView.as_view()),
]
