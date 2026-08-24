"""App-level contract tests: route coexistence, config registry, safety."""
import server.app as app_module
from server.app import app


def test_generic_and_legacy_routes_coexist():
    routes = {(route.path, method) for route in app.routes
              for method in getattr(route, "methods", set())}
    assert ("/api/datasets", "GET") in routes
    assert ("/api/configs", "GET") in routes
    assert ("/api/configs", "POST") in routes
    assert ("/api/configs/{dataset_id}", "DELETE") in routes
    assert ("/api/datasets/{dataset_id}/schema", "GET") in routes
    assert ("/api/datasets/{dataset_id}/entities", "GET") in routes
    assert ("/api/datasets/{dataset_id}/extent", "GET") in routes
    assert ("/api/query", "POST") in routes
    assert ("/api/matrix", "POST") in routes
    assert ("/api/series", "GET") in routes
    assert ("/api/extent", "GET") in routes
    assert ("/api/clear-cache", "GET") in routes


def test_clear_cache_never_touches_a_datasource():
    """The project cache is the only thing clear-cache can truncate; there is
    no direct-source mode anymore, so clearing is always safe."""
    from server.adapter.cache import GenericCache as GenericDatasetCache
    import asyncio

    executed = []

    class FakeCursor:
        async def execute(self, query, params=None):
            executed.append(query)
            return self

    asyncio.run(GenericDatasetCache.clear_all(FakeCursor()))
    assert any("cache_series" in q and "TRUNCATE" in q for q in executed)
    assert not any("eventhistory" in q for q in executed)
