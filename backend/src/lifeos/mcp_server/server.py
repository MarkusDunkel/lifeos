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
   record_answer(session_id, card_id, result, note) BEFORE you say anything else about
   the next card, then give brief feedback, then call next_question.
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

Voice style
- Speak in the subject's language (field "language" of the subject; "en" means English).
- Keep every turn short: at most three sentences, no lists, no markdown, no card ids.
- Feedback: first what was right, then the correct answer in one sentence.
- If the learner pauses, wait. If they ended their answer with "done" or "fertig", the
  answer is complete. Do not interrupt them.

Other requests
- "explain ..." / "read me ...": call read_material(subject, topic) and summarise it
  briefly, then go back to the session.
- "how am I doing" / "am I ready": call progress(subject) and answer in two sentences
  (readiness percent, cards secure, minutes per day still needed).
"""


def _safe(fn: Callable[..., dict]) -> Callable[..., dict]:
    def wrapper(*args: Any, **kwargs: Any) -> dict:
        try:
            return fn(*args, **kwargs)
        except StudyError as exc:
            return {"error": str(exc)}

    return wrapper


def build_mcp(study: StudyService) -> MCPServer:
    server = MCPServer(name="LifeOS Study", instructions=INSTRUCTIONS)

    @server.tool(description="List the subjects with exam date, number of cards and readiness.")
    def list_subjects() -> dict:
        return {"subjects": study.overview()}

    @server.tool(
        description=(
            "Start a practice session. subject: name or folder (e.g. 'Spanish'). "
            "count: number of new questions (default 15). topic: optional filter, e.g. "
            "'Disagreeing'. Returns session_id and the first question."
        )
    )
    def start_session(subject: str, count: int = 15, topic: str | None = None) -> dict:
        return _safe(study.start_session)(subject, count, topic)

    @server.tool(
        description=(
            "Get the next question of a session (or done with a summary). Only after the "
            "previous card was recorded; otherwise the same card comes back with pending=true. "
            "skip=true moves on without grading the current card (learner wants to skip)."
        )
    )
    def next_question(session_id: str, skip: bool = False) -> dict:
        return _safe(study.next_question)(session_id, skip)

    @server.tool(
        description=(
            "Record the learner's answer to a card. result: correct, partial or wrong. "
            "note: what was missing (short). Call this after every answer."
        )
    )
    def record_answer(session_id: str, card_id: str, result: str, note: str = "") -> dict:
        return _safe(study.record)(session_id, card_id, result, note)

    @server.tool(
        description=(
            "Finish a session and return the summary with a confirmation sentence counted by "
            "the server. Refuses (ended=false) while the last card is not recorded; then "
            "record it, or pass skip_pending=true if the learner did not answer it."
        )
    )
    def end_session(session_id: str, skip_pending: bool = False) -> dict:
        return _safe(study.end_session)(session_id, skip_pending)

    @server.tool(
        description="Readiness for a subject: percent secure, weak cards, minutes per day."
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
