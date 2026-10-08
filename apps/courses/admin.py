from django import forms
from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from apps.admin_access import AdminRoleOnlyMixin
from apps.errors import ServiceError
from apps.imgbb import upload_image

from .models import Answer, Course, Enrollment, Lesson, LessonProgress, Question, Quiz


class LessonInline(AdminRoleOnlyMixin, admin.TabularInline):
    model = Lesson
    extra = 0
    fields = ["order", "title", "duration_min", "video_id", "is_free_preview"]
    show_change_link = True


class CourseAdminForm(forms.ModelForm):
    cover_file = forms.ImageField(
        label="Muqova rasmi",
        required=False,
        help_text="Tanlansa — ImgBB'ga yuklanadi va «Cover url» avtomatik to'ldiriladi",
    )

    class Meta:
        model = Course
        fields = [
            "title",
            "slug",
            "description",
            "cover_url",
            "price",
            "owner",
            "instructor_name",
            "is_published",
            "review_status",
            "review_note",
        ]

    def clean(self):
        data = super().clean()
        cover = data.get("cover_file")
        if cover:
            try:
                data["cover_url"] = upload_image(cover, name=data.get("title") or "")
            except ServiceError as exc:
                self.add_error("cover_file", exc.message)
        return data


@admin.register(Course)
class CourseAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    form = CourseAdminForm
    list_display = [
        "id",
        "title",
        "owner",
        "price",
        "is_published",
        "review_status",
        "lessons_count",
        "created_at",
    ]
    list_filter = ["review_status", "is_published"]
    list_select_related = ["owner"]
    autocomplete_fields = ["owner"]
    search_fields = ["title", "slug", "instructor_name"]
    prepopulated_fields = {"slug": ["title"]}
    inlines = [LessonInline]

    readonly_fields = ["cover_preview"]
    fields = [
        "title",
        "slug",
        "description",
        "cover_file",
        "cover_preview",
        "cover_url",
        "price",
        "owner",
        "instructor_name",
        "is_published",
        "review_status",
        "review_note",
    ]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(lessons_total=Count("lessons"))

    @admin.display(description="Muqova")
    def cover_preview(self, obj) -> str:
        if not obj.cover_url:
            return "—"
        return format_html('<img src="{}" style="max-height:160px">', obj.cover_url)

    @admin.display(description="Darslar", ordering="lessons_total")
    def lessons_count(self, obj) -> int:
        return obj.lessons_total


class QuizInline(AdminRoleOnlyMixin, admin.TabularInline):
    model = Quiz
    extra = 0
    max_num = 1
    show_change_link = True
    fields: list = []


@admin.register(Lesson)
class LessonAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    list_display = ["title", "course", "order", "duration_min", "is_free_preview"]
    list_filter = ["is_free_preview", "course"]
    search_fields = ["title", "course__title"]
    list_select_related = ["course"]
    inlines = [QuizInline]


class QuestionInline(AdminRoleOnlyMixin, admin.TabularInline):
    model = Question
    extra = 1
    fields = ["order", "text"]
    show_change_link = True


@admin.register(Quiz)
class QuizAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    list_display = ["__str__", "questions_count"]
    list_select_related = ["lesson"]
    search_fields = ["lesson__title"]
    inlines = [QuestionInline]

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(questions_total=Count("questions"))

    @admin.display(description="Savollar", ordering="questions_total")
    def questions_count(self, obj) -> int:
        return obj.questions_total


class AnswerInline(AdminRoleOnlyMixin, admin.TabularInline):
    model = Answer
    extra = 3
    fields = ["text", "is_correct"]


@admin.register(Question)
class QuestionAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    list_display = ["__str__", "quiz", "order"]
    list_select_related = ["quiz__lesson"]
    search_fields = ["text"]
    inlines = [AnswerInline]

    def has_module_permission(self, request) -> bool:
        return False  # Test sahifasidagi savol havolasi orqali ochiladi


class LessonProgressInline(AdminRoleOnlyMixin, admin.TabularInline):
    model = LessonProgress
    extra = 0
    fields = ["lesson", "quiz_score", "completed_at"]
    readonly_fields = fields

    def has_add_permission(self, request, obj=None) -> bool:
        return False

    def has_delete_permission(self, request, obj=None) -> bool:
        return False


@admin.register(Enrollment)
class EnrollmentAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    list_display = ["student", "course", "created_at", "completed_at"]
    list_filter = ["course"]
    search_fields = ["student__phone", "student__first_name", "course__title"]
    list_select_related = ["student", "course"]
    autocomplete_fields = ["student", "course"]
    readonly_fields = ["completed_at"]
    inlines = [LessonProgressInline]
