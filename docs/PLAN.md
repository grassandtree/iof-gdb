# iof-gdb 작업 계획: Neo4j MCP + RDF/OWL 임포트

## 배경 / 결정 사항
- 목표: IOF Core / BFO 온톨로지와 자체 인스턴스 데이터를 Neo4j 그래프로 넣고, Claude가 MCP로 조회·입력하게 만든다.
- Neo4j: **Aura(클라우드)**. Aura는 n10s 플러그인을 쓸 수 없으므로 RDF 입력은 **rdflib-neo4j**(n10s 없이 동작, n10s 호환 그래프 구조로 저장)로 처리한다.
- MCP는 두 개를 함께 사용한다.
  1. **공식 `mcp-neo4j-cypher`**: 스키마 조회, 일반 Cypher 읽기/쓰기
  2. **직접 구현한 `iof-gdb` MCP 서버**: RDF/OWL 임포트, 온톨로지 탐색, 지식(인스턴스) 입력. 범용 Cypher 도구는 공식 서버에 맡기고 중복 구현하지 않는다.
- 데이터: IOF Core + BFO, 자체 인스턴스 데이터(TTL 등), 이후 지식 입력 기능 확장 가능.
- 도구 체인: Python 3.12 + `uv`.

## 1단계: 프로젝트 골격 (uv)
- `uv python install 3.12` 후 `uv init --package`로 구성
- 의존성: `rdflib`, `rdflib-neo4j`, `neo4j`, `mcp`(FastMCP 포함), `python-dotenv`, `typer`(CLI) / dev: `pytest`, `ruff`
- 구조:
  ```
  pyproject.toml            # [project.scripts] iof-gdb = "iof_gdb.cli:app", iof-gdb-mcp = "iof_gdb.server:main"
  .env.example              # NEO4J_URI=neo4j+s://xxxx.databases.neo4j.io, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE=neo4j
  .mcp.json                 # Claude Code용 MCP 등록 (두 서버)
  data/ontologies/          # IOF Core, BFO 등 로컬 사본 (owl:imports 해석용 catalog)
  data/instances/           # 자체 TTL
  src/iof_gdb/
    config.py               # .env 로드, Neo4j 드라이버 / Neo4jStoreConfig 생성
    prefixes.py             # IOF·BFO·OWL·RDFS·SKOS 등 prefix 맵 (SHORTEN 전략용)
    db.py                   # init(제약조건), stats, 드라이버 헬퍼
    rdf_import.py           # 파일/URL/텍스트 → Neo4j, owl:imports 해석
    ontology.py             # 클래스 검색/설명, 인스턴스 검증용 Cypher 쿼리
    knowledge.py            # 개체·관계 입력 → RDF 트리플 생성 → 임포트
    cli.py                  # typer CLI
    server.py               # FastMCP 서버 (stdio)
  tests/
  ```
- `.env`는 `.gitignore`에 포함되어 있는지 확인한다.

## 2단계: RDF/OWL 임포트 (rdflib-neo4j)
- **DB 초기화** (`db.init`): rdflib-neo4j 필수 제약조건
  `CREATE CONSTRAINT n10s_unique_uri IF NOT EXISTS FOR (r:Resource) REQUIRE r.uri IS UNIQUE`
- **Store 설정**: `Neo4jStoreConfig(auth_data=..., custom_prefixes=PREFIXES, handle_vocab_uri_strategy=HANDLE_VOCAB_URI_STRATEGY.SHORTEN, batching=True)`
  - SHORTEN은 등록되지 않은 네임스페이스를 만나면 실패한다. 그래서 `prefixes.py`에 IOF(`https://spec.industrialontologies.org/ontology/...`), BFO/OBO(`http://purl.obolibrary.org/obo/`), owl/rdfs/skos/dcterms 등을 등록한다. 임포트 전에 미등록 네임스페이스를 미리 탐지해 경고하거나 prefix를 자동 생성하는 옵션을 둔다.
- **임포트 흐름** (`rdf_import.import_source(src, fmt=None, follow_imports=False)`):
  1. 메모리 `rdflib.Graph`로 먼저 파싱(형식은 확장자로 추정: .ttl / .owl·.rdf(rdf/xml) / .nt / .jsonld)
  2. `follow_imports=True`면 `owl:imports`를 재귀적으로 따라간다. `data/ontologies/`의 로컬 사본을 우선 쓰고, 없으면 URL에서 받는다. 이미 로드한 IRI는 건너뛴다.
  3. 네임스페이스를 점검한 뒤 `Graph(store=Neo4jStore(config))`에 트리플을 추가하고 `close(commit_pending_transaction=True)`
  4. 결과 요약 반환(트리플 수, 로드된 온톨로지 IRI, 소요 시간)
- OWL 제약(restriction, unionOf 등)은 blank node로 저장된다(rdflib-neo4j 기본 동작). 1차 버전은 그대로 저장하고, `rdfs__subClassOf` 계층 정리 같은 후처리 Cypher는 후속 과제로 둔다.
- Aura Free 한도(노드 20만 / 관계 40만)는 IOF Core + BFO 규모에서는 충분하다.

## 3단계: CLI
- `uv run iof-gdb init`: 제약조건 생성, 연결 확인
- `uv run iof-gdb import <path|url> [--format ttl] [--follow-imports]`
- `uv run iof-gdb stats`: 노드/관계/라벨 개수
- `uv run iof-gdb fetch-iof`: IOF Core·BFO OWL을 `data/ontologies/`로 내려받기

## 4단계: 직접 구현 MCP 서버 (`iof_gdb.server`, FastMCP, stdio)
공식 서버와 겹치지 않게 RDF/온톨로지 전용 도구만 둔다.

| 도구 | 기능 |
|---|---|
| `import_rdf(source, format?, follow_imports?)` | 파일 경로/URL 임포트 |
| `import_rdf_text(content, format="turtle")` | Claude가 만든 TTL 스니펫을 바로 입력 (지식 입력의 기본 경로) |
| `search_classes(term, limit)` | `rdfs__label`/IRI로 OWL 클래스 검색 |
| `describe_class(iri_or_curie)` | 라벨·정의·상위/하위 클래스·관련 속성 |
| `search_properties(term)` | object/data property 검색 |
| `add_individual(iri, class_iri, label, properties?)` | 클래스가 DB에 있는지 확인한 뒤 인스턴스 생성 |
| `add_relation(subject, property, object)` | 속성이 DB에 있는지 확인한 뒤 관계 생성 |
| `graph_stats()` | 현황 요약 |

- 지식 입력(`add_individual` / `add_relation`)은 내부적으로 rdflib 트리플을 만들어 같은 임포트 경로로 넣는다. 그래서 모든 데이터가 같은 n10s 형식으로 저장된다.
- 리소스 `iof://prefixes`(prefix 목록)를 제공해 Claude가 CURIE를 쓸 때 참고하게 한다.

## 5단계: MCP 등록 (`.mcp.json`, Claude Code)
```json
{
  "mcpServers": {
    "neo4j-cypher": {
      "command": "uvx",
      "args": ["mcp-neo4j-cypher@<고정 버전>", "--transport", "stdio"],
      "env": {
        "NEO4J_URI": "${NEO4J_URI}",
        "NEO4J_USERNAME": "${NEO4J_USERNAME}",
        "NEO4J_PASSWORD": "${NEO4J_PASSWORD}",
        "NEO4J_DATABASE": "neo4j"
      }
    },
    "iof-gdb": {
      "command": "uv",
      "args": ["run", "iof-gdb-mcp"]
    }
  }
}
```
- 비밀번호는 `.mcp.json`에 직접 쓰지 않고 환경변수 확장을 쓴다. `iof-gdb`는 `.env` 또는 환경변수를 읽는다.
- 공식 서버의 `get_neo4j_schema`는 APOC을 쓴다. Aura에는 APOC Core가 기본으로 들어 있다.
- `<고정 버전>`은 구현 시점에 PyPI에서 확인해 고정한다.

## 6단계: README
- Aura 인스턴스 생성 → `.env` 작성 → `init` → `fetch-iof` → `import --follow-imports` → Claude Code에서 MCP 사용 순서를 문서화한다.

## Claude Code 웹에서 작업할 때 확인할 점
- Aura 접속 정보(`NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`)를 웹 환경의 환경변수로 등록해야 한다. 저장소에는 넣지 않는다.
- 웹 샌드박스의 네트워크 설정에서 Aura(`*.databases.neo4j.io`, 포트 7687)와 IOF/BFO 다운로드 주소로 나가는 접속이 허용되는지 확인한다. 막혀 있으면 단위 테스트까지만 진행하고, 통합 테스트와 실제 적재는 로컬에서 한다.

## 검증
1. `uv run pytest`: 단위 테스트(형식 추정, prefix 점검, owl:imports 해석). 메모리 Graph만 쓰므로 DB가 필요 없다.
2. 통합 테스트(`NEO4J_URI`가 있을 때만, `@pytest.mark.integration`): 작은 TTL 임포트 → `MATCH (r:Resource) RETURN count(r)` 확인 → 정리
3. `uv run iof-gdb init && uv run iof-gdb import data/ontologies/<IOF Core> --follow-imports && uv run iof-gdb stats`로 Aura에 IOF+BFO 적재 확인
4. `npx @modelcontextprotocol/inspector uv run iof-gdb-mcp`로 각 도구 호출 테스트
5. Claude Code에서 `/mcp`로 두 서버 연결 확인 후 질문으로 확인: "BFO의 material entity 하위 IOF 클래스 보여줘"(cypher 서버), "펌프 P-101 개체를 추가해줘"(iof-gdb 서버)
