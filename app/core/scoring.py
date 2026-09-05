from app.core.security import utcnow


def finalize_session(session, correct_map: dict, answers: list, total: int) -> tuple[int, float]:
    """Finalize a session server-side. Score is stored out of 20; percent is returned for the UI.

    `correct_map` maps question_id -> correct option letter. Unanswered questions count as wrong.
    """
    correct = sum(1 for a in answers if correct_map.get(a.question_id) == a.chosen_opt)
    percent = (correct / total * 100) if total else 0.0
    session.score = round(correct / total * 20, 2) if total else 0.0
    session.status = "completed"
    session.ended_at = utcnow()
    return correct, percent
