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


class FakeResult:
    def __init__(self, record):
        self._record = record

    def single(self):
        return self._record


class FakeTx:
    def __init__(self, responder, log):
        self.responder, self.log = responder, log

    def run(self, query, **params):
        self.log.append(query)
        return FakeResult(self.responder(query))


class FakeSession:
    def __init__(self, responder, log):
        self.responder, self.log = responder, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute_write(self, fn, *args):
        return fn(FakeTx(self.responder, self.log), *args)


class FakeSessionDriver:
    """Driver stand-in for code that uses session().execute_write()."""

    def __init__(self, responder):
        self.responder = responder  # query -> record dict
        self.queries = []

    def session(self, **kwargs):
        return FakeSession(self.responder, self.queries)
