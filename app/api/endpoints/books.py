import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Path

from app.models import Book
from app.services.openlibrary_service import OpenLibraryError, OpenLibraryService, get_openlibrary_service
from app.services.supabase_service import SupabaseService, get_supabase_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/{book_id}", response_model=Book)
async def get_book(
    book_id: str = Path(pattern=r"^OL\d+W$", description="Open Library work key, e.g. OL893414W"),
    supabase_service: SupabaseService = Depends(get_supabase_service),
    openlibrary: OpenLibraryService = Depends(get_openlibrary_service),
) -> Book:
    """A book from a recommendation, with its description (fetched from Open Library the first time)."""
    book = await supabase_service.get_book(book_id)
    if book is None:
        # Books are cached when they're recommended, so an unknown id wasn't one of ours
        raise HTTPException(status_code=404, detail="Book not found")

    if book.description_checked_at is None:
        try:
            book.description = await openlibrary.get_description(book_id)
        except OpenLibraryError as e:
            # Leave it unchecked so the next open tries again; the client says it couldn't load
            logger.warning("Couldn't load description for %s: %s", book_id, e)
            return book
        book.description_checked_at = datetime.now(timezone.utc)
        await supabase_service.save_book_description(book_id, book.description)
    return book
