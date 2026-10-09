from django.urls import path

from . import views_admin

urlpatterns = [
    path("mailboxes/", views_admin.AdminMailboxesView.as_view()),
    path("mailboxes/<uuid:pk>/", views_admin.AdminMailboxDetailView.as_view()),
]
