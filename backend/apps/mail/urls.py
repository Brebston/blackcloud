from django.urls import path

from . import views

urlpatterns = [
    path("mailbox/", views.MailboxView.as_view()),
    path("mailbox/client-password/", views.ClientPasswordView.as_view()),
    path("folders/", views.FoldersView.as_view()),
    path("messages/", views.MessagesView.as_view()),
    path("messages/<int:uid>/", views.MessageDetailView.as_view()),
    path("messages/<int:uid>/attachments/<int:index>/", views.AttachmentView.as_view()),
    path("render/", views.RenderView.as_view()),
    path("send/", views.SendView.as_view()),
]
