"""MCP server for AI assistants (ChatGPT, Claude, agents): study sessions over the vault.

Only study tools are exposed: no general vault access. The server is mounted under a
secret path (``LIFEOS_MCP_PATH_TOKEN``) until OAuth is in place.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mcp.server.mcpserver import MCPServer

from lifeos.modules.study.service import StudyError, StudyService

INSTRUCTIONS = """\
You are a strict but friendly exam coach. LifeOS holds the learner's question banks and
progress and decides which card comes next; you are the voice and the examiner.

Session flow
1. If the learner names no subject, call list_subjects and ask which one.
2. Call start_session(subject, count). It returns the first question. Say in one short
   sentence what you will practise (e.g. "15 cards, mostly the ones you missed").
3. For every card: ask ONE question, then wait for the answer. After the answer, call
   record_answer(session_id, card_id, result, note) BEFORE you say anything else, then
   give brief feedback, then call next_question. Every tool result has a field
   "coach_rule": follow it, it tells you the next step.
   Every answer must be saved: next_question only gives a new card after record_answer.
   If it returns pending=true, record the answer to that card first. If the learner wants
   to skip a card without answering, call next_question with skip=true (not graded).
   "I don't know" or asking for the solution is an answer: record it as wrong.
4. When next_question returns done, or the learner says stop / finish / Schluss, call
   end_session. If it returns ended=false, the last card is not saved: record it if the
   learner answered it, otherwise call end_session with skip_pending=true.
5. After end_session, read the field "confirmation" out loud word for word (for example
   "6 answers saved, 2 correct. Readiness 8 percent."). It is counted by the server and
   tells the learner that their progress is safe. Then add at most one sentence.

How to ask (vary the type from card to card)
- Definition -> term: read the definition, ask for the term.
- Gap sentence (field "Lückensatz"): read it aloud, say "blank" for the gap.
- Term -> meaning: say the term, ask what it means and for an example sentence.
- Function -> phrase (field "Funktion"): describe the situation, ask for a fitting phrase.
- Phrase -> function: say the phrase, ask when you would use it.
- Other cards (field "Muss-Punkte"): ask the card title as a question in your own words.
Never read the answer, the must-points or the card id aloud before the learner answered.

Grading (be honest)
- correct: the required term/phrase or all must-points, small grammar or word-form
  differences are fine.
- partial: close, only part of the must-points, or right only after a hint.
- wrong: wrong, missing, or the learner asked for the solution.
For phrases any phrase of the same card is correct; a fitting phrase that is not on the
card is partial. Put what was missing into the note (max one sentence).

The learner has the final word (they know the exam better than you)
- "Known" with the answer: if the learner says "known", "trivial", "kenn ich" or "zu
  banal" instead of an answer or right after it, call record_answer with result=known
  for that card. It moves to the top box and only comes back shortly before the exam.
- If the learner disagrees with your grade, in whatever words ("that was right", "das
  war richtig", "count it as correct"), call correct_answer(session_id, card_id, result)
  right away. Do not argue; confirm in one short sentence and go on.
- "The last one" / "the previous card" means the card in the field "previous" of the
  latest next_question result; its coach_rule names the card id to use.
- Use known and correct_answer only when the learner asks for it, never on your own.
  Skipping (skip=true) stays for cards the learner does not want to answer now.

Voice style
- Speak in the subject's language (field "language" of the subject; "en" means English).
- Keep every turn short: at most three sentences, no lists, no markdown, no card ids.
- Feedback: say the grade you saved ("I saved that as partial."), then what was right,
  then the correct answer in one sentence. So the learner can object right away.
- If the learner pauses, wait. If they ended their answer with "done" or "fertig", the
  answer is complete. Do not interrupt them.

Honesty
- Never say saved, marked, corrected or known unless the tool call returned success
  (recorded=true, corrected=true or ended=true). Saying it does not save anything.
- If a tool returns an error, tell the learner in one sentence. Do not talk over it.
- If you are unsure whether an answer was saved, call record_answer instead of guessing.

Other requests
- "explain ..." / "read me ...": call read_material(subject, topic) and summarise it
  briefly, then go back to the session.
- "how am I doing" / "am I ready": call progress(subject) and answer in two sentences
  (readiness percent, cards secure, minutes per day still needed). Exam dates are set per
  topic; if a subject has several exams, name the next one and its topics.
"""


HONESTY = "Never say saved, marked or corrected without a successful result from this tool."
CARD_RULE = (
    "Ask this card. After the learner answers: call record_answer BEFORE you say anything "
    "else. If the learner says known / trivial, record result=known. Never claim to have "
    "saved without its result."
)
ERROR_RULE = (
    "The tool failed: nothing was saved or changed. Tell the learner this error in one "
    "sentence. Do not claim success."
)


def _safe(fn: Callable[..., dict]) -> Callable[..., dict]:
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except StudyError as exc:
            return {"error": str(exc), "coach_rule": ERROR_RULE}

    return wrapper


def _objection(card: str) -> str:
    return (
        "If the learner objects to that grade or calls it known, call correct_answer for "
        f"card {card}."
    )


def question_rule(question: dict) -> dict:
    """Add the coach rule to a question: save first, and which card "the last one" is."""
    if "error" in question:
        return question
    if question.get("done"):
        rule = "No more cards. Call end_session now and read its confirmation word for word."
    else:
        rule = CARD_RULE
        previous = question.get("previous")
        if previous:
            rule += (
                f" Previous card {previous['card']} was saved as {previous['result']}. "
                + _objection(previous["card"])
            )
    return question | {"coach_rule": rule}


def saved_rule(result: dict) -> dict:
    if "error" in result:
        return result
    rule = (
        f"Saved on the server as {result['result']}. Now give brief feedback that starts "
        "with the saved grade, then call next_question. " + _objection(result["card"])
    )
    return result | {"coach_rule": rule}


def corrected_rule(result: dict) -> dict:
    if "error" in result:
        return result
    if result.get("corrected"):
        rule = (
            f"Changed on the server: card {result['card']} is now {result['result']}. "
            "Confirm in one short sentence, then go on with the current card."
        )
    else:
        rule = (
            f"Nothing changed: card {result['card']} was already saved as "
            f"{result['result']}. Tell the learner in one short sentence."
        )
    return result | {"coach_rule": rule}


def ended_rule(result: dict) -> dict:
    if "error" in result:
        return result
    if result.get("ended") is False:
        rule = (
            "The session is NOT ended and the last answer is NOT saved. Record it with "
            "record_answer if the learner answered, otherwise call end_session with "
            "skip_pending=true. Do not say the session is saved."
        )
    else:
        rule = "Read the field confirmation out loud word for word, then at most one sentence."
    return result | {"coach_rule": rule}


def build_mcp(study: StudyService) -> MCPServer:
    server = MCPServer(name="LifeOS Study", instructions=INSTRUCTIONS)

    @server.tool(
        description=(
            "List the subjects with their exam dates (a subject can have several: exam dates "
            "are set per topic), number of cards and readiness."
        )
    )
    def list_subjects() -> dict:
        return {"subjects": study.overview()}

    @server.tool(
        description=(
            "Start a practice session. subject: name or folder (e.g. 'Spanish'). "
            "count: number of new questions (default 15). topic: optional filter, e.g. "
            "'Disagreeing'. Returns session_id and the first question. After the learner "
            "answers it, call record_answer first; only then call next_question."
        )
    )
    def start_session(subject: str, count: int = 15, topic: str | None = None) -> dict:
        result = _safe(study.start_session)(subject, count, topic)
        if "question" in result:
            result["question"] = question_rule(result["question"])
            result["coach_rule"] = result["question"]["coach_rule"]
        return result

    @server.tool(
        description=(
            "Get the next question of a session (or done with a summary). Only after the "
            "previous card was recorded; otherwise the same card comes back with pending=true. "
            "skip=true moves on without grading the current card (learner wants to skip). "
            "After the learner answers this card, call record_answer first; only then call "
            "next_question. The field previous is the last saved card: use its id when the "
            "learner objects to the last grade."
        )
    )
    def next_question(session_id: str, skip: bool = False) -> dict:
        return question_rule(_safe(study.next_question)(session_id, skip))

    @server.tool(
        description=(
            "MUST be called after every answer of the learner, before you give feedback or "
            "ask the next question. Without it the answer is lost. result: correct, partial, "
            "wrong, or known (the learner says known / trivial instead of or with the "
            "answer; moves the card to the top box). note: what was missing (short). " + HONESTY
        )
    )
    def record_answer(session_id: str, card_id: str, result: str, note: str = "") -> dict:
        return saved_rule(_safe(study.record)(session_id, card_id, result, note))

    @server.tool(
        description=(
            "MUST be called when the learner asks to change a grade or to mark an answered "
            "card as known; saying it without calling this tool changes nothing. result: "
            "correct, partial, wrong or known. Affects the latest saved answer to that card. "
            "For 'the last one' use the card id from previous in the latest next_question "
            "result. " + HONESTY
        )
    )
    def correct_answer(session_id: str, card_id: str, result: str, note: str = "") -> dict:
        return corrected_rule(
            _safe(study.correct)(card_id, result, session_id=session_id, note=note)
        )

    @server.tool(
        description=(
            "Finish a session and return the summary with a confirmation sentence counted by "
            "the server. Refuses (ended=false) while the last card is not recorded; then "
            "record it, or pass skip_pending=true if the learner did not answer it. " + HONESTY
        )
    )
    def end_session(session_id: str, skip_pending: bool = False) -> dict:
        return ended_rule(_safe(study.end_session)(session_id, skip_pending))

    @server.tool(
        description=(
            "Readiness for a subject: percent secure, weak cards, minutes per day until the "
            "next exam, and per topic with that topic's exam date."
        )
    )
    def progress(subject: str) -> dict:
        return _safe(study.progress)(subject)

    @server.tool(
        description=(
            "Read learning material of a subject. Without topic: list of sections. With "
            "topic: the matching section text (e.g. 'Disagreeing', 'Deadlock')."
        )
    )
    def read_material(subject: str, topic: str | None = None) -> dict:
        return _safe(study.material)(subject, topic)

    return server
