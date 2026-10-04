from rest_framework.routers import SimpleRouter

from .views import CalendarViewSet, EventViewSet

router = SimpleRouter()
router.register("events", EventViewSet, basename="event")
router.register("", CalendarViewSet, basename="calendar")
urlpatterns = router.urls
