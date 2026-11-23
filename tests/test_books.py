import asyncio
import time
from typing import List, Optional

import httpx
import pytest

from app.models import OpenLibraryDoc, OpenLibraryEdition, OpenLibraryEditions
from app.schemas import BookPick
from app.services.openlibrary_service import (
    OpenLibraryError, OpenLibraryService, RateLimiter, best_match, display_title, lookup_key, normalize_title, parse_description,
)


def pick(title: str, author: str, year: Optional[int] = None) -> BookPick:
    return BookPick(title=title, author=author, year=year, reason="r")


def doc(title: str, authors: List[str], year: Optional[int] = None, editions: int = 10, key: str = "OL1W",
        edition_title: Optional[str] = None) -> OpenLibraryDoc:
    matched = OpenLibraryEditions(docs=[OpenLibraryEdition(title=edition_title)]) if edition_title else None
    return OpenLibraryDoc(key=f"/works/{key}", title=title, author_name=authors,
                          first_publish_year=year, edition_count=editions, editions=matched)


@pytest.mark.parametrize("raw, expected", [
    ("Dune", "dune"),
    ("The Name of the Wind", "name of the wind"),
    ("Dune: Deluxe Edition", "dune"),
    ("Harry Potter and the Philosopher's Stone", "harry potter and the philosophers stone"),
    ("Cien años de soledad", "cien anos de soledad"),
    ("Pride & Prejudice", "pride and prejudice"),
    ("Neuromancer (Sprawl, #1)", "neuromancer"),
    ("1984", "nineteen eighty four"),
    ("Nineteen Eighty-Four", "nineteen eighty four"),
    ("Catch-22", "catch twenty two"),
    ("Fahrenheit 451", "fahrenheit four hundred fifty one"),
    ("2001: A Space Odyssey", "two thousand one"),
    ("The 39 Steps", "thirty nine steps"),
])
def test_normalize_title(raw, expected):
    assert normalize_title(raw) == expected


def test_matches_the_right_work_among_same_titled_books():
    docs = [doc("Dune", ["Brian Herbert"], 1999, key="OL2W"), doc("Dune", ["Frank Herbert"], 1965, 155, key="OL893414W")]
    assert best_match(pick("Dune", "Frank Herbert", 1965), docs).work_id == "OL893414W"


def test_same_surname_different_person_is_rejected():
    assert best_match(pick("Dune", "Frank Herbert", 1965), [doc("Dune", ["Brian Herbert"], 1999)]) is None


def test_wrong_catalogue_year_doesnt_reject_the_real_book():
    # Live Open Library data: Pale Fire (1962) is catalogued as first published in 1945
    docs = [doc("Pale Fire", ["Vladimir Nabokov"], 1945, 59, key="OL627081W"),
            doc("Novels 1955-1962 (Lolita / Lolita. A Screenplay / Pale Fire / Pnin)", ["Vladimir Nabokov"], 1996, 4),
            doc("Pale Fire", ["Andrew Field", "Vladimir Nabokov"], 1967, 1, key="OL45966660W")]
    assert best_match(pick("Pale Fire", "Vladimir Nabokov", 1962), docs).work_id == "OL627081W"


def test_the_real_work_beats_a_stray_duplicate_with_a_closer_year():
    # Live Open Library data for q="The Plague"&author="Albert Camus": we used to pick the stray
    docs = [doc("La Peste", ["Albert Camus"], 1942, 280, key="OL1230715W", edition_title="The Plague"),
            doc("Collected fiction", ["Albert Camus"], 1960, 7, edition_title="Collected fiction."),
            doc("Plague", ["Albert Camus"], 1948, 2, key="OL44575184W", edition_title="Plague")]
    assert best_match(pick("The Plague", "Albert Camus", 1947), docs).work_id == "OL1230715W"


def test_numbers_match_spelled_out_titles_and_adaptations_lose():
    # Live Open Library data for q="1984"&author="George Orwell": we used to pick the adaptation
    docs = [doc("Nineteen Eighty-Four", ["George Orwell"], 1949, 727, key="OL1168083W", edition_title="Nineteen eighty-four"),
            doc("1984 (adaptation)", ["Michael Dean", "George Orwell"], 2003, 4, key="OL11326416W", edition_title="1984"),
            doc("1984 with Connections", ["George Orwell"], 2000, 2, key="OL1168151W", edition_title="1984")]
    nineteen_eighty_four = pick("1984", "George Orwell", 1949)
    assert best_match(nineteen_eighty_four, docs).work_id == "OL1168083W"
    assert display_title(nineteen_eighty_four, docs[0]) == "Nineteen Eighty-Four"


def test_co_authored_books_match_either_author_and_the_real_work_wins():
    # Live: a stray Gaiman-only entry (7 editions) sits next to the real co-written work
    docs = [doc("Good Omens", ["Neil Gaiman"], 1990, 7, key="OLstrayW"),
            doc("Good Omens", ["Terry Pratchett", "Neil Gaiman"], 1990, 120, key="OLrealW")]
    assert best_match(pick("Good Omens", "Neil Gaiman", 1990), docs).work_id == "OLrealW"


def test_year_breaks_ties_between_equally_common_works():
    docs = [doc("Rebecca", ["Daphne du Maurier"], 1980, 200, key="OLreprintW"),
            doc("Rebecca", ["Daphne du Maurier"], 1938, 200, key="OL36633W")]
    assert best_match(pick("Rebecca", "Daphne du Maurier", 1938), docs).work_id == "OL36633W"


@pytest.mark.parametrize("ours, theirs, same", [
    ("J.R.R. Tolkien", "J. R. R. Tolkien", True),
    ("Liu Cixin", "Cixin Liu", True),
    ("Arthur Conan Doyle", "Sir Arthur Conan Doyle", True),
    ("Ursula Le Guin", "Ursula K. Le Guin", True),
    ("Frank Herbert", "Brian Herbert", False),
    ("Stephen King", "Owen King", False),
])
def test_author_names(ours, theirs, same):
    assert (best_match(pick("Some Book", ours), [doc("Some Book", [theirs])]) is not None) == same


def test_same_title_by_a_different_author_is_rejected():
    assert best_match(pick("Atonement", "Ian McEwan", 2001), [doc("Atonement", ["Jane Smith"], 2001)]) is None


def test_a_different_book_with_a_similar_title_is_rejected():
    assert best_match(pick("Dune", "Frank Herbert", 1965), [doc("Dune Messiah", ["Frank Herbert"], 1969)]) is None


def test_unknown_catalogue_year_doesnt_block_a_match():
    assert best_match(pick("Dune", "Frank Herbert", 1965), [doc("Dune", ["Frank Herbert"], None)]) is not None


def test_accents_name_order_articles_and_subtitles():
    assert best_match(pick("One Hundred Years of Solitude", "Gabriel Garcia Marquez", 1967),
                      [doc("Cien años de soledad", ["Gabriel García Márquez"], 1967)]) is None  # different title text
    assert best_match(pick("One Hundred Years of Solitude", "Gabriel Garcia Marquez", 1967),
                      [doc("One Hundred Years of Solitude", ["Gabriel García Márquez"], 1967)]) is not None
    assert best_match(pick("The Three-Body Problem", "Liu Cixin", 2008),
                      [doc("Three-Body Problem", ["Cixin Liu"], 2008)]) is not None
    assert best_match(pick("Dune", "Frank Herbert"), [doc("Dune: 50th Anniversary Edition", ["Frank Herbert"])]) is not None


def test_translated_work_matches_through_its_edition_title():
    # Live Open Library data for q="The Stranger Albert Camus": the novel is catalogued as "L'étranger"
    docs = [doc("L’étranger", ["Albert Camus"], 1942, 471, key="OL1230613W", edition_title="The Stranger"),
            doc("Albert Camus, The stranger", ["McCarthy, Patrick"], 1988, 2, edition_title="Albert Camus, The stranger"),
            doc("The Stranger by Albert Camus", [], None, 1, edition_title="The Stranger by Albert Camus")]
    stranger = pick("The Stranger", "Albert Camus", 1942)
    match = best_match(stranger, docs)
    assert match.work_id == "OL1230613W"
    assert display_title(stranger, match) == "The Stranger"   # what the user sees, not "L'étranger"


def test_display_title_avoids_oddly_cased_edition_records():
    godot = pick("Waiting for Godot", "Samuel Beckett", 1953)
    work = doc("En attendant Godot", ["Samuel Beckett"], 1952, 120, edition_title="WAITING FOR GODOT")
    assert best_match(godot, [work]) is not None
    assert display_title(godot, work) == "Waiting for Godot"
    # When the work's own title matches, the catalogue's version is kept
    assert display_title(pick("No Exit", "Jean-Paul Sartre"),
                         doc("No Exit (and Three Other Plays)", ["Jean-Paul Sartre"], edition_title="No exit (Huis clos)")) \
        == "No Exit (and Three Other Plays)"


def test_prefers_the_work_with_most_editions_among_duplicates():
    docs = [doc("Dune", ["Frank Herbert"], 1965, editions=2, key="OLstrayW"),
            doc("Dune", ["Frank Herbert"], 1965, editions=155, key="OL893414W")]
    assert best_match(pick("Dune", "Frank Herbert", 1965), docs).work_id == "OL893414W"


def test_lookup_key_ignores_wording_differences():
    assert lookup_key("The Hobbit", "J.R.R. Tolkien") == lookup_key("Hobbit", "J. R. R. Tolkien")
    assert lookup_key("Dune", "Frank Herbert") == lookup_key("Dune: Deluxe Edition", "Frank Herbert Jr.")
    # Known limitation: name order changes the surname, so such a pick misses the cache and is searched
    # again (still correct, one extra Open Library call)
    assert lookup_key("Three-Body Problem", "Liu Cixin") != lookup_key("Three-Body Problem", "Cixin Liu")


@pytest.mark.parametrize("raw, expected", [
    ("A desert planet.", "A desert planet."),
    ({"type": "/type/text", "value": " A desert planet.\n"}, "A desert planet."),
    ("   ", None),
    (None, None),
    (42, None),
])
def test_parse_description_handles_both_shapes(raw, expected):
    assert parse_description(raw) == expected


def test_cover_falls_back_to_the_matched_edition():
    work = OpenLibraryDoc(key="/works/OL1W", title="Some Book", cover_i=None,
                          editions=OpenLibraryEditions(docs=[OpenLibraryEdition(title="Some Book", cover_i=777)]))
    assert work.to_book().cover_id == 777
    assert work.model_copy(update={"cover_i": 555}).to_book().cover_id == 555  # the work's own cover wins


def test_to_book_keeps_a_useful_handful():
    raw = OpenLibraryDoc(key="/works/OL893414W", title="Dune", author_name=["Frank Herbert"], author_key=["OL79034A"],
                         isbn=["0441013597", "9780441013593", "9780340960196"], subject=[f"s{i}" for i in range(50)],
                         ia=[f"ia{i}" for i in range(20)], cover_i=11481354, number_of_pages_median=605)
    book = raw.to_book()
    assert book.id == "OL893414W"
    assert book.authors[0].id == "OL79034A"
    assert book.isbns[:2] == ["9780441013593", "9780340960196"]  # ISBN-13 first
    assert len(book.subjects) == 10 and len(book.ia_ids) == 5
    assert book.page_count == 605 and book.description is None


def test_rate_limiter_spaces_concurrent_calls():
    async def run() -> float:
        limiter = RateLimiter(per_second=20)
        start = time.perf_counter()
        await asyncio.gather(*(limiter.wait() for _ in range(5)))
        return time.perf_counter() - start

    assert asyncio.run(run()) >= 4 * 0.05 * 0.9  # 5 calls -> 4 gaps of 50ms


def service_with(handler) -> OpenLibraryService:
    service = OpenLibraryService()
    service.limiter = RateLimiter(per_second=1000)  # tests shouldn't wait
    service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=service.client.headers)
    return service


def test_search_identifies_itself_and_requests_only_the_needed_fields():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["user_agent"] = request.headers["user-agent"]
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json={"docs": [{"key": "/works/OL893414W", "title": "Dune",
                                                    "author_name": ["Frank Herbert"], "first_publish_year": 1965}]})

    async def run():
        service = service_with(handler)
        try:
            return await service.find_book(pick("Dune", "Frank Herbert", 1965))
        finally:
            await service.close()

    found = asyncio.run(run())
    assert found.work_id == "OL893414W"
    assert seen["user_agent"].startswith("PikFlix/1.0 (https://pikflix.app")
    assert seen["params"]["q"] == "Dune" and seen["params"]["author"] == "Frank Herbert"  # q= so edition titles match too
    assert set(seen["params"]["fields"].split(",")) == {*OpenLibraryDoc.model_fields, "editions.title", "editions.cover_i"}


def failing_service(handler) -> tuple[list, Exception | None]:
    async def run():
        service = service_with(handler)
        try:
            results = await service.search("Dune", "Frank Herbert")
            try:
                await service.get_description("OL893414W")
            except OpenLibraryError as e:
                return results, e
            return results, None
        finally:
            await service.close()

    return asyncio.run(run())


def test_open_library_errors_mean_no_results_and_a_retryable_description():
    assert failing_service(lambda request: httpx.Response(503))[0] == []


def test_timeouts_are_handled_not_raised_as_500s():
    # Live: a /works/{id}.json read timed out and the details endpoint returned a 500
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    results, error = failing_service(timeout)
    assert results == []                       # search: the pick is dropped, the stream continues
    assert isinstance(error, OpenLibraryError)  # description: the caller decides (endpoint retries later)
