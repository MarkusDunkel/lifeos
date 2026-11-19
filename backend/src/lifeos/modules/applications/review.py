"""The application as a review package: readable letter and CV, PDFs only on request.

The writer stores the letter and the CV as Markdown next to ``draft.json`` and describes
them in ``review.yaml`` (see ``lifeos.modules.reviews.package``). The review page shows the
Markdown for comments on the content and generates the PDF beside it for the layout.
"""

from __future__ import annotations

from typing import Any

import yaml

from lifeos.modules.applications.model import Draft


def letter_markdown(draft: Draft) -> str:
    letter = draft.letter
    lines = [f"# {letter.subject}", "", "  \n".join(letter.recipient), "", letter.salutation, ""]
    for paragraph in letter.paragraphs:
        lines += [paragraph.strip(), ""]
    lines.append(letter.closing)
    return "\n".join(lines) + "\n"


def cv_markdown(cv: dict[str, Any]) -> str:
    """The resolved CV (``resolve_cv``) as Markdown: headline, profile, stations, skills."""
    lines = [f"# {cv['headline']}", "", cv["profile"].strip(), ""]
    if cv["positions"]:
        lines += ["## Experience", ""]
    for pos in cv["positions"]:
        where = ", ".join(p for p in (pos.get("company"), pos.get("place")) if p)
        lines += [f"### {pos['title']}", "", f"{where} · {pos.get('period', '')}".strip(" ·"), ""]
        lines += [f"- {b['text']}" for b in pos["bullets"]] + [""]
    if cv["education"]:
        lines += ["## Education", ""]
    for edu in cv["education"]:
        where = f"{edu.get('institution', '')} · {edu.get('period', '')}".strip(" ·")
        lines += [f"### {edu.get('degree', '')}", "", where, ""]
        if edu.get("detail"):
            lines += [str(edu["detail"]), ""]
        lines += [f"- {b['text']}" for b in edu.get("bullets", [])]
        if edu.get("bullets"):
            lines.append("")
    skills = dict(cv.get("skills") or {})
    groups = skills.pop("groups", None) or []
    if groups:
        skills.pop("technical", None)  # the groups replace the single line
    skills = {k: v for k, v in skills.items() if v}
    if skills or groups:
        lines += ["## Skills", ""]
    if groups:
        lines += ["- **technical:**"] + [f"  - {g['label']}: {g['items']}" for g in groups]
    if skills or groups:
        lines += [f"- **{k}:** {v}" for k, v in skills.items()] + [""]
    return "\n".join(lines).rstrip() + "\n"


def package_yaml(
    title: str, ticket: str | None, files: dict[str, str], labels: dict[str, str]
) -> str:
    """``review.yaml`` for an application folder (``files`` as in the writer)."""
    documents = [
        {"label": labels["letter"], "source": files["letter_text"],
         "view": {"type": "pdf-generated", "generator": "application-letter",
                  "input": files["draft"]}},
        {"label": labels["cv"], "source": files["cv_text"],
         "view": {"type": "pdf-generated", "generator": "application-cv",
                  "input": files["draft"]}},
        {"label": labels["notes"], "source": files["notes"], "view": {"type": "markdown"}},
        {"label": labels["research"], "source": files["research"], "view": {"type": "markdown"}},
    ]  # fmt: skip
    data: dict[str, Any] = {"title": title}
    if ticket:
        data["ticket"] = ticket
    data["documents"] = documents
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
