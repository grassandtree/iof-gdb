"""A tiny stand-in for neo4j.Driver.execute_query used by unit tests."""


class FakeRecord(dict):
    def data(self):
        return dict(self)


class FakeDriver:
    def __init__(self, responder):
        self.responder = responder  # (query, params) -> list[dict]
        self.queries = []

    def execute_query(self, query, params=None, **kwargs):
        params = params or {}
        self.queries.append((query, params))
        return [FakeRecord(r) for r in self.responder(query, params)], None, None

    def close(self):
        pass
