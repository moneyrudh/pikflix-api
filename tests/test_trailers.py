import asyncio

import httpx

from app.models import Movie, TMDBVideo
from app.services.tmdb_service import TMDBService, pick_trailer


def video(key: str, **overrides) -> TMDBVideo:
    fields = {"key": key, "site": "YouTube", "type": "Trailer", "official": True, "iso_639_1": "en",
              "size": 1080, "published_at": "2020-01-01T00:00:00.000Z"}
    return TMDBVideo.model_validate({**fields, **overrides})


def test_no_videos_means_no_trailer():
    assert pick_trailer([]) is None


def test_ignores_non_youtube_and_non_trailer_types():
    videos = [video("vimeo", site="Vimeo"), video("clip", type="Clip"), video("bts", type="Behind the Scenes")]
    assert pick_trailer(videos) is None


def test_falls_back_to_teaser_only_when_no_trailer():
    assert pick_trailer([video("teaser", type="Teaser")]).key == "teaser"
    assert pick_trailer([video("teaser", type="Teaser"), video("trailer", official=False)]).key == "trailer"


def test_ranking_official_then_language_then_size_then_earliest():
    assert pick_trailer([video("fan", official=False, size=2160), video("official", size=720)]).key == "official"
    assert pick_trailer([video("es", iso_639_1="es", size=2160), video("en", size=720)]).key == "en"
    assert pick_trailer([video("hd", size=1080), video("4k", size=2160)]).key == "4k"
    # Re-release promos are uploaded later; the original campaign trailer wins
    assert pick_trailer([video("original"), video("rerelease", published_at="2024-06-01T00:00:00.000Z")]).key == "original"
    assert pick_trailer([video("undated", published_at=None), video("dated")]).key == "dated"


def test_details_replace_videos_with_trailer():
    payload = {
        "id": 603, "title": "The Matrix",
        "videos": {"results": [
            {"key": "teaser1", "site": "YouTube", "type": "Teaser", "official": True, "iso_639_1": "en"},
            {"key": "m8e-FF8MsqU", "site": "YouTube", "type": "Trailer", "official": True,
             "iso_639_1": "en", "size": 1080, "name": "Official Trailer", "published_at": "2019-01-01T00:00:00.000Z"},
        ]},
    }
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, json=payload)

    async def run():
        service = TMDBService()
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await service.get_movie_details(603)
        finally:
            await service.close()

    data = asyncio.run(run())

    assert seen["params"] == {"append_to_response": "videos"}
    assert "videos" not in data  # would break the DB upsert: there is no `videos` column
    assert data["trailer"] == {"key": "m8e-FF8MsqU", "name": "Official Trailer", "official": True, "language": "en"}
    assert data["videos_checked_at"]
    assert Movie.model_validate(data).trailer.key == "m8e-FF8MsqU"


def test_details_without_videos_still_marks_checked():
    async def run():
        service = TMDBService()
        service.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": 1, "name": "Show", "videos": {"results": []}})))
        try:
            return await service.get_show_details(1)
        finally:
            await service.close()

    data = asyncio.run(run())
    assert data["trailer"] is None
    assert data["videos_checked_at"]  # so the row isn't refetched on every cache hit
