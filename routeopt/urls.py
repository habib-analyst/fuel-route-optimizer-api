from django.urls import path

from . import views

urlpatterns = [
    path("api/route/", views.route, name="route"),
    path("api/health/", views.health, name="health"),
]
