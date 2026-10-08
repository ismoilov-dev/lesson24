"""Learning flow: lesson completion, quiz grading, course completion."""

from dataclasses import dataclass
from uuid import UUID

from django.db import transaction
from django.db.models import Case, PositiveSmallIntegerField, Value, When
from django.utils import timezone

from apps.certificates.models import Certificate
from apps.errors import ServiceError
from bot import notify

from .models import Answer, Course, Enrollment, Lesson, LessonProgress, Question, Quiz


@dataclass
class CompletionResult:
    lesson_completed: bool
    quiz_score: int | None
    passed: bool
    course_completed: bool
    certificate_uid: UUID | None = None


def enroll_free(student, course: Course) -> Enrollment:
    """Narxi 0 bo'lgan kursga chek va admin tasdig'isiz yozilish."""
    if not course.is_published:
        raise ServiceError("Kurs topilmadi.", code="not_found", status=404)
    if course.price > 0:
        raise ServiceError("Bu kurs pullik — to'lov orqali sotib oling.", code="payment_required")
    enrollment, created = Enrollment.objects.get_or_create(student=student, course=course)
    if not created:
        raise ServiceError("Bu kurs allaqachon ochilgan.", code="already_enrolled")
    return enrollment


def grade_quiz(quiz: Quiz, answer_ids: list[int]) -> int:
    """Percent of questions answered correctly. One answer per question; unanswered = wrong."""
    total = quiz.questions.count()
    if total == 0:
        return 100
    answers = list(
        Answer.objects.filter(pk__in=set(answer_ids), question__quiz=quiz).values_list(
            "question_id", "is_correct"
        )
    )
    if len(answers) != len(set(answer_ids)):
        raise ServiceError("Javoblar bu testga tegishli emas.", code="invalid_answers")
    question_ids = [question_id for question_id, _ in answers]
    if len(question_ids) != len(set(question_ids)):
        raise ServiceError("Har bir savolga bitta javob bering.", code="invalid_answers")
    correct = sum(1 for _, is_correct in answers if is_correct)
    return round(correct * 100 / total)


@transaction.atomic
def complete_lesson(
    enrollment: Enrollment, lesson: Lesson, answer_ids: list[int] | None = None
) -> CompletionResult:
    """Mark a lesson done. A lesson with a quiz is done only when the score reaches the pass mark.

    Retakes are allowed; the best score is kept.
    """
    if lesson.course_id != enrollment.course_id:
        raise ServiceError("Dars bu kursga tegishli emas.", code="wrong_course")

    quiz = Quiz.objects.filter(lesson=lesson).first()
    score = None
    if quiz is not None:
        if not answer_ids:
            raise ServiceError("Bu darsda test bor: javoblarni yuboring.", code="answers_required")
        score = grade_quiz(quiz, answer_ids)
    passed = score is None or score >= Quiz.PASS_PERCENT

    progress, _ = LessonProgress.objects.select_for_update().get_or_create(
        enrollment=enrollment, lesson=lesson
    )
    newly_completed = passed and progress.completed_at is None
    if score is not None and (progress.quiz_score is None or score > progress.quiz_score):
        progress.quiz_score = score
    if newly_completed:
        progress.completed_at = timezone.now()
    progress.save(update_fields=["quiz_score", "completed_at"])

    course_completed = (
        complete_course_if_done(enrollment) if newly_completed else False
    ) or enrollment.completed_at is not None
    certificate_uid = (
        Certificate.objects.filter(enrollment=enrollment).values_list("uid", flat=True).first()
        if course_completed
        else None
    )
    return CompletionResult(
        lesson_completed=progress.completed_at is not None,
        quiz_score=score,
        passed=passed,
        course_completed=course_completed,
        certificate_uid=certificate_uid,
    )


@transaction.atomic
def complete_course_if_done(enrollment: Enrollment) -> bool:
    """When every lesson is done: set `completed_at` and issue the certificate.

    Returns True only on the first completion.
    """
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if enrollment.completed_at is not None:
        return False
    # Ikki indeksli COUNT: Lesson→progress JOIN kursning barcha o'quvchilari qatorlarini tortadi
    total = Lesson.objects.filter(course_id=enrollment.course_id).count()
    done = LessonProgress.objects.filter(enrollment=enrollment, completed_at__isnull=False).count()
    if total == 0 or done < total:
        return False
    enrollment.completed_at = timezone.now()
    enrollment.save(update_fields=["completed_at"])
    Certificate.objects.get_or_create(enrollment=enrollment)
    notify.course_completed(enrollment.pk)
    return True


@transaction.atomic
def replace_quiz(lesson: Lesson, questions: list[dict]) -> Quiz:
    """Replace the lesson's quiz with `questions` (`[{text, answers: [{text, is_correct}]}]`).

    Whole-quiz replace in one transaction: simpler for the editor than per-item CRUD,
    and three bulk queries instead of one INSERT per answer.
    """
    quiz, _ = Quiz.objects.get_or_create(lesson=lesson)
    quiz.questions.all().delete()
    created = Question.objects.bulk_create(
        Question(quiz=quiz, order=i, text=q["text"]) for i, q in enumerate(questions, start=1)
    )
    Answer.objects.bulk_create(
        Answer(question=question, text=a["text"], is_correct=a["is_correct"])
        for question, q in zip(created, questions, strict=True)
        for a in q["answers"]
    )
    return quiz


@transaction.atomic
def reorder_lessons(course, lesson_ids: list[int]) -> None:
    """Set lesson order to the position in `lesson_ids` (must list every lesson exactly once).

    One UPDATE; the deferred unique constraint is checked at commit.
    """
    current = set(Lesson.objects.filter(course=course).values_list("pk", flat=True))
    if len(lesson_ids) != len(set(lesson_ids)) or set(lesson_ids) != current:
        raise ServiceError(
            "Kursning barcha darslarini bir martadan yuboring.", code="invalid_order"
        )
    Lesson.objects.filter(course=course).update(
        order=Case(
            *(When(pk=pk, then=Value(i)) for i, pk in enumerate(lesson_ids, start=1)),
            output_field=PositiveSmallIntegerField(),
        )
    )


# --- teacher kursini tekshiruvdan o'tkazish ---------------------------------------------


@transaction.atomic
def submit_for_review(course: Course) -> Course:
    """Teacher: qoralama yoki qaytarilgan kursni admin tekshiruviga yuborish."""
    course = Course.objects.select_for_update().get(pk=course.pk)
    if course.review_status not in (Course.ReviewStatus.DRAFT, Course.ReviewStatus.REJECTED):
        raise ServiceError(
            "Kurs allaqachon tekshiruvda yoki tasdiqlangan.", code="invalid_review_status"
        )
    if not course.lessons.exists():
        raise ServiceError("Avval kamida bitta dars qo'shing.", code="no_lessons")
    course.review_status = Course.ReviewStatus.PENDING
    course.review_note = ""
    course.save(update_fields=["review_status", "review_note", "updated_at"])
    notify.course_submitted(course.pk)
    return course


@transaction.atomic
def approve_course(course: Course) -> Course:
    """Admin: tasdiqlash — kurs katalogda nashr qilinadi."""
    course = Course.objects.select_for_update().get(pk=course.pk)
    course.review_status = Course.ReviewStatus.APPROVED
    course.review_note = ""
    course.is_published = True
    course.save(update_fields=["review_status", "review_note", "is_published", "updated_at"])
    notify.course_reviewed(course.pk)
    return course


@transaction.atomic
def reject_course(course: Course, note: str) -> Course:
    """Admin: izoh bilan qaytarish — kurs katalogdan olinadi, teacher tuzatib qayta yuboradi."""
    note = (note or "").strip()
    if not note:
        raise ServiceError("Qaytarish sababini yozing.", code="note_required")
    course = Course.objects.select_for_update().get(pk=course.pk)
    course.review_status = Course.ReviewStatus.REJECTED
    course.review_note = note[:500]
    course.is_published = False
    course.save(update_fields=["review_status", "review_note", "is_published", "updated_at"])
    notify.course_reviewed(course.pk)
    return course
