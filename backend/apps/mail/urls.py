from django.urls import path

from . import views

urlpatterns = [
    path("mailboxes/", views.MailboxesView.as_view()),
    path("mailbox/", views.MailboxView.as_view()),
    path("mailbox/client-password/", views.ClientPasswordView.as_view()),
    path("folders/", views.FoldersView.as_view()),
    path("messages/", views.MessagesView.as_view()),
    path("messages/<int:uid>/", views.MessageDetailView.as_view()),
    path("messages/<int:uid>/attachments/<int:index>/", views.AttachmentView.as_view()),
    path("render/", views.RenderView.as_view()),
    path("send/", views.SendView.as_view()),
    path("drafts/", views.DraftsView.as_view()),
    path("drafts/<int:uid>/", views.DraftDetailView.as_view()),
    path("scheduled/", views.ScheduledListView.as_view()),
    path("scheduled/<uuid:pk>/cancel/", views.ScheduledCancelView.as_view()),
    path("signatures/", views.SignaturesView.as_view()),
    path("signatures/<uuid:pk>/", views.SignatureDetailView.as_view()),
    path("confidential/", views.ConfidentialListView.as_view()),
    path("confidential/<uuid:pk>/revoke/", views.ConfidentialRevokeView.as_view()),
]
