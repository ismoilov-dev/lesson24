from django.urls import path
from rest_framework.routers import SimpleRouter

from .admin_api import AdminCourseViewSet, AdminEnrollmentViewSet, AdminLessonViewSet
from .teacher_api import TeacherCourseViewSet, TeacherLessonViewSet, TeacherStatsView
from .views import CourseViewSet, LessonViewSet, MyCoursesView

router = SimpleRouter()
router.register("courses", CourseViewSet, basename="course")
router.register("lessons", LessonViewSet, basename="lesson")
router.register("teacher/courses", TeacherCourseViewSet, basename="teacher-course")
router.register("teacher/lessons", TeacherLessonViewSet, basename="teacher-lesson")
router.register("admin/courses", AdminCourseViewSet, basename="admin-course")
router.register("admin/lessons", AdminLessonViewSet, basename="admin-lesson")
router.register("admin/enrollments", AdminEnrollmentViewSet, basename="admin-enrollment")

urlpatterns = [
    path("me/courses/", MyCoursesView.as_view(), name="my-courses"),
    path("teacher/stats/", TeacherStatsView.as_view(), name="teacher-stats"),
    *router.urls,
]
