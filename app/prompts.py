"""
Prompt templates for Claude API calls.

Prompts are composed from a shared base + content-type-specific injections.
Each content type only adds what's unique to it.
"""

from app.config import BOOK_PICKS_REQUESTED
from app.models import ContentTypeMode

_RECOMMENDATION_BASE = (
    "You are a recommendation assistant. Given a user's requirements, "
    "provide exactly 9 diverse recommendations that match their criteria.\n\n"
    "Make sure the results are diverse in terms of era, style, and popularity.\n"
    "If the user searched for a specific title or a prompt that warrants less than 9 responses, "
    "smartly recommend the remaining titles that are the closest match.\n"
    "You MUST give exactly 9 recommendations. No more. No less.\n"
    "When the user provides follow-up requests, use the conversation history to understand context "
    "and avoid recommending titles that were already suggested."
)

_MOVIE_CONTEXT = (
    "\n\nFocus exclusively on movies. "
    "Recommend based on title, director, genre, era, and themes. "
    "Set the content_type field to \"movie\" for every recommendation."
)

_SHOW_CONTEXT = (
    "\n\nFocus exclusively on TV shows. "
    "Recommend based on show name, creator, network, genre, era, and themes. "
    "Set the content_type field to \"show\" for every recommendation."
)

_BOTH_CONTEXT = (
    "\n\nRecommend a mix of movies AND TV shows. "
    "Use the content_type field to indicate whether each recommendation is a \"movie\" or a \"show\". "
    "Aim for roughly 5 movies and 4 shows, but adjust the split based on what best matches the query. "
    "If the query leans more toward serialized storytelling, favor shows; if it leans toward standalone stories, favor movies."
)

_CONTENT_TYPE_CONTEXTS = {
    ContentTypeMode.MOVIE: _MOVIE_CONTEXT,
    ContentTypeMode.SHOW: _SHOW_CONTEXT,
    ContentTypeMode.BOTH: _BOTH_CONTEXT,
}

_CONTENT_TYPE_LABELS = {
    ContentTypeMode.MOVIE: "movies",
    ContentTypeMode.SHOW: "TV shows",
    ContentTypeMode.BOTH: "movies and TV shows",
}


_BOOK_PROMPT = (
    "You are a book recommendation assistant. Given a user's requirements, "
    f"provide exactly {BOOK_PICKS_REQUESTED} diverse book recommendations that match their criteria.\n\n"
    "Make sure the results are diverse in terms of era, style, and popularity.\n"
    "Only recommend books you are confident exist. Give each title as it was published (without series "
    "numbering), the primary author's full name, and the year of first publication, or null if you are unsure. "
    "These are used to look the book up, so accuracy matters more than variety.\n"
    f"You MUST give exactly {BOOK_PICKS_REQUESTED} recommendations. No more. No less.\n"
    "When the user provides follow-up requests, use the conversation history to understand context "
    "and avoid recommending titles that were already suggested."
)


def get_book_system_prompt() -> str:
    return _BOOK_PROMPT


def get_book_user_message(query: str, history: list | None = None) -> str:
    return _user_message("books", query, history)


def get_recommendation_system_prompt(content_type: ContentTypeMode) -> str:
    """Compose system prompt from base + content-type-specific injection."""
    context = _CONTENT_TYPE_CONTEXTS[content_type]
    return _RECOMMENDATION_BASE + context


def get_recommendation_user_message(query: str, content_type: ContentTypeMode, history: list | None = None) -> str:
    """Build user message with optional conversation history."""
    return _user_message(_CONTENT_TYPE_LABELS[content_type], query, history)


def _user_message(label: str, query: str, history: list | None) -> str:
    if not history:
        return f"Find me {label} that match this description: {query}"

    parts = ["Conversation history:\n"]
    for i, turn in enumerate(history, 1):
        parts.append(f"[Query {i}]: \"{turn.query}\"")
        recs = ", ".join(
            f"{r.title} ({r.year})" if r.year else r.title
            for r in turn.recommendations
        )
        parts.append(f"[Recommended]: {recs}\n")

    parts.append(f"[Current request]: \"{query}\"")
    return "\n".join(parts)
