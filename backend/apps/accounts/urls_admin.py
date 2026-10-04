from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("users", views.AdminUserViewSet, basename="admin-user")
router.register("invites", views.AdminInviteViewSet, basename="admin-invite")
router.register("audit", views.AdminAuditViewSet, basename="admin-audit")
urlpatterns = router.urls
