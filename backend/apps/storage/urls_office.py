from django.urls import path

from . import views_office

urlpatterns = [
    path("config/<uuid:pk>/", views_office.OfficeConfigView.as_view()),
    path("file/<uuid:pk>/", views_office.OfficeFileView.as_view()),
    path("callback/<uuid:pk>/", views_office.OfficeCallbackView.as_view()),
]
