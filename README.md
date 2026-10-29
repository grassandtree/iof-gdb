# iof-gdb

#neo4j #IOF #BFO

IOF Core / BFO 온톨로지와 자체 인스턴스 데이터를 **Neo4j Aura**에 넣고, Claude가 **MCP**로 조회·입력하게 만드는 프로젝트.

- Aura는 n10s 플러그인을 못 쓰기 때문에 RDF 입력은 [rdflib-neo4j](https://github.com/neo4j-labs/rdflib-neo4j)로 한다 (n10s 없이 동작하고, n10s와 같은 그래프 구조로 저장).
- MCP 서버 두 개를 같이 쓴다.
  | 서버 | 역할 |
  |---|---|
  | `neo4j-cypher` (공식 [`mcp-neo4j-cypher`](https://pypi.org/project/mcp-neo4j-cypher/)) | 스키마 조회, 일반 Cypher 읽기/쓰기 |
  | `iof-gdb` (이 저장소) | RDF/OWL 임포트, 온톨로지 탐색, 지식(인스턴스) 입력 |

## 준비물

- Python 3.12, [uv](https://docs.astral.sh/uv/)
- Neo4j Aura 인스턴스 (Free 한도 노드 20만 / 관계 40만이면 충분. IOF Core + BFO + AnnotationVocabulary는 약 5,900 트리플 → 노드 약 1,500 / 관계 약 2,800)
- Claude Code (MCP 사용 시)

## 1. Aura 인스턴스 만들기

1. [console.neo4j.io](https://console.neo4j.io)에서 인스턴스를 만든다.
2. 생성할 때 내려받는 credentials 파일에 `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`(, `NEO4J_DATABASE`)가 있다. 비밀번호는 이때만 보여주니 안전하게 보관한다.

## 2. 설치와 `.env`

```bash
uv sync
```

```bash
cp .env.example .env
```

`.env`를 열어 접속 정보를 채운다. `.env`는 `.gitignore`에 들어 있어서 커밋되지 않는다. 같은 이름의 환경변수가 이미 있으면 그쪽이 우선한다.

| 변수 | 설명 |
|---|---|
| `NEO4J_URI` | `neo4j+s://xxxx.databases.neo4j.io` |
| `NEO4J_USERNAME` / `NEO4J_PASSWORD` | 접속 계정 |
| `NEO4J_DATABASE` | 기본 `neo4j`. credentials 파일에 다른 값이 있으면 그 값 |
| `IOF_GDB_INSTANCE_NS` | (선택) 자체 인스턴스 IRI 네임스페이스. CURIE `inst:`로 쓴다. 기본 `https://example.org/iof-gdb/instance/` |
| `IOF_GDB_PROFILE` | (선택) 온톨로지 프로필. 기본 `iof`. [프로필](#프로필-온톨로지별-설정) 참고 |
| `IOF_GDB_MCP_TOKEN` | (선택) HTTP 모드 MCP 서버의 Bearer 토큰. [5절](#5-open-webui에서-mcp-쓰기-streamable-http) 참고 |

## 3. 초기화 → 온톨로지 받기 → 적재

```bash
uv run iof-gdb init
```
연결을 확인하고 rdflib-neo4j가 요구하는 제약조건 `n10s_unique_uri`(`:Resource(uri)` 유일)를 만든다.

```bash
uv run iof-gdb fetch
```
프로필의 기본 목록(iof 프로필이면 IOF Core)과 그 `owl:imports`(BFO 2020, IOF AnnotationVocabulary)를 `data/ontologies/`에 받고, IRI → 파일 대응표 `catalog-v001.xml`(Protégé 호환)을 만든다. 받은 파일은 git에 넣지 않는다.

```bash
uv run iof-gdb import data/ontologies/Core.rdf --follow-imports
```
`--follow-imports`는 `owl:imports`를 재귀적으로 따라가며 로컬 사본을 먼저 쓰고, 없으면 URL에서 받는다. 이미 읽은 온톨로지(import IRI, 선언된 온톨로지 IRI, versionIRI 모두)는 건너뛴다.

```bash
uv run iof-gdb import data/instances/example-pump.ttl
```

```bash
uv run iof-gdb stats
```

### CLI 요약

| 명령 | 설명 |
|---|---|
| `iof-gdb init` | 연결 확인, 제약조건 생성 |
| `iof-gdb import <path\|url> [--format ttl] [--follow-imports] [--auto-prefix]` | RDF 임포트. 형식은 확장자 → Content-Type → 내용 순으로 추정 (`.ttl`, `.owl`/`.rdf`(RDF/XML), `.nt`, `.n3`, `.jsonld`, 번역표 `.tsv`) |
| `iof-gdb stats` | 노드/관계 수, 라벨·관계 타입별 개수 |
| `iof-gdb derive` | 파생 구조(`:BlankNode`, `SUBCLASS_OF`) 다시 만들기 (임포트 때마다 자동 실행) |
| `iof-gdb reset [--yes]` | DB의 모든 노드·관계 삭제 (확인 후, 제약조건은 유지) |
| `iof-gdb fetch [IRI ...] [--dest DIR] [--no-follow-imports]` | 온톨로지 로컬 사본 받기 (기본: 프로필의 `[fetch]` 목록). `fetch-iof`는 같은 명령의 옛 이름 |
| `iof-gdb profile [이름\|경로.toml]` | 프로필 확인·검증 (prefix, 클래스·라벨·정의로 쓰는 저장 이름, fetch 목록) |

## 4. Claude Code에서 MCP 쓰기

저장소 루트의 [`.mcp.json`](.mcp.json)에 두 서버가 등록돼 있다. 둘 다 `.env`(또는 환경변수, 환경변수가 우선)에서 접속 정보를 읽으므로 따로 export할 필요가 없고, `.mcp.json`에는 비밀번호가 없다.

- `neo4j-cypher`는 래퍼 `iof-gdb-cypher`로 띄운다. 공식 `mcp-neo4j-cypher`는 환경변수만 읽는데, Claude Code는 `.mcp.json`의 `${VAR}`를 `.env`가 아니라 자기를 실행한 셸에서 채운다. 래퍼가 `iof-gdb`와 같은 방식으로 설정을 읽어 **환경변수로** 넘긴 뒤(명령줄 인자로 넘기면 `ps`에 보인다) `uvx mcp-neo4j-cypher@0.6.0`을 실행한다. 버전은 `.mcp.json`의 인자에서 바꾼다.

1. `.env`를 채운다 (위 2번).
2. 저장소 루트에서 `claude` 실행 → 프로젝트 MCP 서버 사용을 승인 → `/mcp`로 두 서버가 연결됐는지 확인.
3. 이렇게 물어본다.
   - "BFO의 material entity 하위 IOF 클래스 보여줘" → `neo4j-cypher`
   - "펌프 P-101 개체를 추가해줘" → `iof-gdb`의 `search_classes` → `add_individual`

`neo4j-cypher`의 `get_neo4j_schema`는 APOC을 쓴다. Aura에는 APOC Core가 기본으로 들어 있다.

### `iof-gdb` MCP 도구

| 도구 | 기능 |
|---|---|
| `import_rdf(source, format?, follow_imports?, auto_prefix?)` | 파일 경로/URL 임포트 |
| `import_rdf_text(content, format="turtle", auto_prefix?)` | Claude가 만든 TTL 스니펫을 바로 입력 (지식 입력 기본 경로) |
| `search_classes(term, limit)` | 라벨·동의어(모든 언어)·IRI로 클래스 검색. 결과에 언어별 라벨(`labels`) 포함 |
| `describe_class(iri_or_curie)` | 라벨·정의(언어별 포함)·상위/하위 클래스·추론된 상위 클래스·제약(restriction)·domain/range로 연결된 속성 |
| `search_properties(term, limit, kind?)` | object/datatype property 검색 (domain, range 포함) |
| `add_individual(iri, class_iri, label?, properties?)` | 클래스가 DB에 있는지 확인한 뒤 인스턴스 생성 |
| `add_relation(subject, property, object)` | 속성(object property)과 양쪽 개체가 DB에 있는지 확인한 뒤 관계 생성 |
| `graph_stats()` | 현황 요약 |
| 리소스 `iof://prefixes` | prefix 목록 (CURIE 작성용) |

`add_individual` / `add_relation`도 내부적으로 rdflib 트리플을 만들어 파일 임포트와 같은 경로로 넣는다. 그래서 모든 데이터가 같은 형식으로 저장된다.

MCP Inspector로 도구를 직접 호출해볼 수도 있다.

```bash
npx @modelcontextprotocol/inspector uv run iof-gdb-mcp
```

### Neo4j Agent Skills

[`.claude/settings.json`](.claude/settings.json)에 Neo4j 공식 스킬 모음 [neo4j-contrib/neo4j-skills](https://github.com/neo4j-contrib/neo4j-skills) 플러그인이 등록돼 있다 (Cypher 작성·최적화, 그래프 모델링, Python 드라이버, MCP, Aura 등). 저장소를 신뢰하면 Claude Code가 마켓플레이스 추가와 플러그인 설치를 제안한다. 버전은 고정하지 않아서 업스트림 업데이트가 그대로 반영된다. `/plugin list`로 확인하고, 수동으로 넣으려면:

```
/plugin marketplace add https://github.com/neo4j-contrib/neo4j-skills.git
/plugin install neo4j-skills@neo4j-skills-marketplace
```

## 5. Open WebUI에서 MCP 쓰기 (Streamable HTTP)

Open WebUI(v0.6.31 이상)는 MCP를 **Streamable HTTP로만** 직접 붙인다 (stdio 미지원, [문서](https://docs.openwebui.com/features/mcp)). 그래서 두 서버를 HTTP 모드로 따로 띄운다. Claude Code용 stdio 설정(`.mcp.json`)은 그대로 둔다.

### 서버 띄우기

`iof-gdb`: 토큰은 `.env`나 환경변수로만 받는다 (명령줄 인자로 넘기면 `ps`에 보인다). OS에 상관없이 가장 쉬운 방법은 토큰을 만들어 `.env`에 `IOF_GDB_MCP_TOKEN=<값>` 한 줄로 넣는 것이다.

```bash
uv run python -c "import secrets; print(secrets.token_hex(24))"
```

`.env` 대신 셸에서 정하려면 macOS/Linux는 `export IOF_GDB_MCP_TOKEN="$(openssl rand -hex 24)"`, Windows PowerShell은 `$env:IOF_GDB_MCP_TOKEN = uv run python -c "import secrets; print(secrets.token_hex(24))"`를 쓴다. 이 경우 서버도 같은 창에서 띄워야 한다.

```bash
uv run iof-gdb-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

| 옵션 | 기본값 | 설명 |
|---|---|---|
| `--transport` | `stdio` | `streamable-http`로 HTTP 모드 |
| `--host` / `--port` / `--path` | `127.0.0.1` / `8000` / `/mcp` | 바인드 주소, 포트, 엔드포인트 |
| `--allowed-host` | 없음 | localhost 외에 받을 Host 헤더 (여러 번 지정 가능). `*`는 검사 끄기 |
| `IOF_GDB_MCP_TOKEN` (환경변수) | 없음 | 설정하면 `Authorization: Bearer <토큰>`이 없는 요청은 401 |

`neo4j-cypher`: 같은 래퍼로 띄우고 인자만 HTTP로 바꾼다. 공식 서버에는 토큰 인증이 없으니 localhost에만 바인딩하고, 읽기만 시킬 거면 `--read-only`를 붙인다. 엔드포인트는 `/mcp/`다.

```bash
uv run iof-gdb-cypher mcp-neo4j-cypher@0.6.0 --transport http --server-host 127.0.0.1 --server-port 8001 --read-only
```

### Open WebUI에 등록

Admin Settings → External Tools → **+** 에서 Type을 **MCP (Streamable HTTP)**로 하고 서버마다 하나씩 추가한다.

| 서버 | URL | 인증 |
|---|---|---|
| `iof-gdb` | `http://localhost:8000/mcp` | Bearer, 값은 `IOF_GDB_MCP_TOKEN` |
| `neo4j-cypher` | `http://localhost:8001/mcp/` | 없음 |

**Open WebUI가 Docker 안에서 돌 때**: 컨테이너 안의 `localhost`는 컨테이너 자신이다.
- URL은 `http://host.docker.internal:8000/mcp`처럼 바꾼다.
- 서버는 그 Host 헤더를 허용해야 한다. `iof-gdb`는 `--allowed-host host.docker.internal`, `neo4j-cypher`는 `--allowed-hosts host.docker.internal`(쉼표 구분)을 붙인다.
- Linux Docker는 컨테이너를 `--add-host=host.docker.internal:host-gateway`로 띄워야 하고, 이때 `host.docker.internal`은 docker0 브리지(보통 `172.17.0.1`)를 가리킨다. 그래서 서버를 `127.0.0.1`이 아니라 `--host 172.17.0.1`(cypher는 `--server-host 172.17.0.1`)로 띄운다. `0.0.0.0`은 LAN 전체에 열리니 토큰 없이 쓰지 않는다.

### 모델 설정

- 모델 설정에서 Function Calling을 **Native**로 둔다.
- MCP 서버가 주는 사용 안내문이 모델에 전달되지 않을 수 있다. 시스템 프롬프트에 다음을 넣으면 정확도가 오른다.

  ```
  Neo4j에는 IOF Core·BFO 온톨로지가 rdflib-neo4j(n10s) 형식으로 들어 있다. 모든 노드는 :Resource {uri},
  어휘는 prefix__localName (라벨 owl__Class, 속성 rdfs__label, 관계 rdfs__subClassOf).
  IRI는 CURIE로 써도 된다: bfo:BFO_0000040, iofconstr:PieceOfEquipment, inst:P-101.
  지식 입력 전에는 search_classes / search_properties로 클래스와 속성을 먼저 찾는다.
  ```

## 그래프 구조

rdflib-neo4j의 `SHORTEN` 전략을 쓴다. 술어와 `rdf:type` 대상 IRI는 `prefix__localName`으로 바뀐다.

| RDF | Neo4j |
|---|---|
| 모든 IRI 주어/목적어 | `(:Resource {uri: "..."})` |
| `ex:x rdf:type owl:Class` | 라벨 `owl__Class` |
| `ex:x rdfs:label "pump"` | 속성 `rdfs__label` |
| `ex:x rdfs:label "펌프"@ko` | 속성 `` `rdfs__label@ko` `` (아래 "저장 규칙" 참고) |
| `ex:x skos:altLabel "a", "b"` (다중 값으로 지정된 속성) | 배열 `skos__altLabel: ["a", "b"]` |
| `ex:x rdfs:subClassOf ex:y` | 관계 `[:rdfs__subClassOf]` |
| blank node (restriction, unionOf 등) | `uri`가 내용 기반 해시(`:` 없음)인 `:Resource:BlankNode` |
| (파생) 이름 있는 클래스 사이의 상위 클래스 | `[:SUBCLASS_OF {inferred: false\|true}]` |

### 파생 구조 (`SUBCLASS_OF`, `:BlankNode`)

`rdfs:subClassOf`는 진짜 상위 클래스(`Pump ⊑ Equipment`)와 조건(`Equipment ⊑ hasRole some EquipmentRole`, blank node)을 같은 관계로 저장한다. 그래서 `[:rdfs__subClassOf*]`를 따라가면 라벨 없는 blank node가 섞여 나온다. 임포트가 끝날 때마다 다음을 다시 만든다 ([`derive.py`](src/iof_gdb/derive.py)). 원본 `rdfs__subClassOf`는 그대로 둔다.

- **`:BlankNode` 라벨**: 모든 blank node에 붙는다. `WHERE NOT n:BlankNode`로 거른다.
- **`SUBCLASS_OF`**: 이름 있는 클래스 사이에만 만든다.
  - `inferred: false`: `rdfs:subClassOf`에서 복사한 것
  - `inferred: true`: `owl:equivalentClass`/`rdfs:subClassOf`의 `owl:intersectionOf`에 이름 있는 클래스가 들어 있을 때 (`A ≡ B ⊓ …`이면 `A ⊑ B`). 합집합(`unionOf`) 멤버는 상위 클래스가 아니라서 만들지 않는다. 이 규칙 하나만 쓰는 것이고 추론기는 아니다. 완전한 추론이 필요하면 임포트 전에 [ROBOT](https://robot.obolibrary.org/)(`reason`, ELK) 같은 OWL 추론기로 처리한다.

IOF Core + BFO에서는 복사 125개, 추론 3개(예: manufacturer ⊑ organization)가 생긴다. 이름이 대문자인 건 RDF에서 온 `prefix__local` 이름과 구분하기 위해서다.

```cypher
MATCH (c:Resource {uri: "https://spec.industrialontologies.org/ontology/construct/PieceOfEquipment"})
      -[:SUBCLASS_OF*]->(sup)
RETURN sup.rdfs__label, sup.`rdfs__label@ko`
```

### 저장 규칙

rdflib-neo4j로 쓰기 직전에 그래프를 한 번 정리한다 ([`storage.py`](src/iof_gdb/storage.py)).

- **언어 태그**: 언어가 붙은 값은 언어별 속성에도 들어간다. `rdfs:label "펌프"@ko` → `` n.`rdfs__label@ko` ``. 태그는 주 언어 코드로 맞춘다 (`en-US` → `en`). 기본 속성 `rdfs__label`에는 태그 없는 값, 없으면 프로필 `languages`의 첫 언어(`en`) 값이 들어간다. 그래서 기존 쿼리는 그대로 동작하고, 나중에 한국어 라벨만 추가로 넣어도 영어 기본값을 덮어쓰지 않는다. 한국어만 있는 노드는 MCP 도구가 `@ko` 값으로 대신 보여준다. Cypher에서 `@`가 들어간 이름은 백틱으로 감싼다.
- **다중 값**: 프로필 `[storage] multivalued`에 있는 속성은 배열로 저장된다 (언어별 속성 포함). 목록에 없는 속성에 같은 주어의 값이 여러 개 있으면, Neo4j에 하나만 남게 되므로 **아무것도 쓰기 전에 멈추고** 목록을 알려준다. 그 속성을 프로필에 추가하면 된다. 이미 단일 값으로 저장된 속성을 나중에 목록에 넣어도, 임포트할 때 기존 값을 배열로 감싼 뒤 추가한다.
- **blank node**: rdflib 표준 정규화(RGDA1)로 내용에서 결정적인 ID를 만든다. 같은 파일을 다시 넣어도 같은 노드에 MERGE되어 중복이 생기지 않는다. IOF Core + BFO 전체에서 약 16초가 더 걸린다.

W3C prefix(`rdf`, `rdfs`, `owl`, `xsd`, `skos`, `dc`, `dcterms`)는 [`prefixes.py`](src/iof_gdb/prefixes.py)에 고정돼 있고, 도메인 prefix는 프로필에 있다. 아래는 `iof` 프로필 기준이다. Cypher에서 백틱 없이 쓰도록 하이픈을 뺐다 (IOF 파일의 `iof-av:` → 여기선 `iofav:`).

| prefix | 네임스페이스 |
|---|---|
| `bfo` | `http://purl.obolibrary.org/obo/` (BFO_0000040 → `bfo__BFO_0000040`) |
| `iofconstr` | `https://spec.industrialontologies.org/ontology/construct/` (IOF Core 클래스·속성, Release 202603) |
| `iofav` | `https://spec.industrialontologies.org/ontology/annotation/` (IOF 어노테이션) |
| `iofind` | `https://spec.industrialontologies.org/ontology/individual/` |
| `iofcore` | `https://spec.industrialontologies.org/ontology/core/Core/` |
| `inst` | `IOF_GDB_INSTANCE_NS` |
| `rdf`, `rdfs`, `owl`, `xsd`, `skos`, `dc`, `dcterms` | W3C / Dublin Core |

**미등록 네임스페이스**: SHORTEN은 prefix가 없는 네임스페이스를 만나면 실패한다. 그래서 임포트 전에 전부 점검하고, 없으면 아무것도 쓰기 전에 목록과 함께 멈춘다. `--auto-prefix`(MCP: `auto_prefix=true`)를 주면 prefix를 만들어 쓰고, n10s처럼 `(:_NsPrefDef)` 노드에 저장해 다음 임포트에서도 같은 이름을 쓴다.

예: BFO material entity 하위의 IOF 클래스

```cypher
MATCH (c:owl__Class)-[:SUBCLASS_OF*1..]->(:Resource {uri: "http://purl.obolibrary.org/obo/BFO_0000040"})
WHERE c.uri STARTS WITH "https://spec.industrialontologies.org/"
RETURN c.uri, c.rdfs__label ORDER BY c.rdfs__label
```

## 프로필 (온톨로지별 설정)

코드는 "rdflib-neo4j로 넣은 OWL/RDFS 그래프"라면 어디든 쓸 수 있는 공용 부분이고, **어떤 온톨로지인가**에 따라 달라지는 값만 프로필 파일(TOML)에 모았다.

| 층 | 어디에 | 예 |
|---|---|---|
| 저장 규칙 | 코드 (rdflib-neo4j SHORTEN) | `:Resource {uri}`, `prefix__localName` |
| 모델링 언어 (표준) | 코드, 이름은 W3C IRI에서 계산 | `rdfs:subClassOf`, `owl:Restriction`, `rdfs:domain` |
| 도메인 (온톨로지별) | **프로필** | 도메인 prefix, 클래스로 볼 타입, 라벨·동의어·정의 속성, 기본 fetch 목록, MCP 안내문 |

쿼리는 `owl__Class` 같은 이름을 문자열로 박아두지 않고, 표준 IRI(`owl:Class`)와 프로필에서 계산한다. 그래서 prefix 이름이 바뀌어도 쿼리가 따라간다.

- 내장 프로필: [`iof`](src/iof_gdb/profiles/iof.toml) (기본값), [`generic`](src/iof_gdb/profiles/generic.toml) (W3C 어휘만)
- 선택: `.env`나 환경변수에 `IOF_GDB_PROFILE=generic`, 또는 `IOF_GDB_PROFILE=profiles/my-onto.toml`처럼 파일 경로를 준다.
- 확인: `uv run iof-gdb profile`로 현재 프로필이 실제로 어떤 저장 이름을 쓰는지 볼 수 있다. MCP에서는 `iof://profile` 리소스로 같은 내용을 보여준다.

새 온톨로지용 프로필은 `generic.toml`을 복사해서 만든다.

```toml
name = "my-onto"
description = "사내 설비 온톨로지"

[prefixes]                 # 도메인 네임스페이스 (W3C prefix는 자동 포함)
eq = "https://example.com/onto/equipment#"

[vocabulary]               # 값은 CURIE 또는 전체 IRI
class_types = ["owl:Class", "rdfs:Class"]          # search_classes / add_individual이 "클래스"로 볼 rdf:type
label_properties = ["rdfs:label", "skos:prefLabel"]  # 앞에 있을수록 우선
synonym_properties = ["skos:altLabel"]               # 검색에만 사용
definition_properties = ["eq:definition", "skos:definition", "rdfs:comment"]

[storage]
languages = ["ko", "en"]                             # 첫 언어가 기본 속성에 들어감
multivalued = ["skos:altLabel", "skos:example"]      # 배열로 저장할 속성

[fetch]
default_iris = ["https://example.com/onto/equipment"]  # `iof-gdb fetch` 기본 목록

[mcp]
instructions = """
설비는 eq: 네임스페이스를 쓴다. 예: eq:Pump, eq:hasPart.
"""
```

프로필을 바꾸면 MCP 서버를 다시 띄워야 한다 (안내문은 서버 시작 시 정해진다). 잘못된 값(하이픈 들어간 prefix 이름, 등록 안 된 네임스페이스의 IRI, W3C prefix 재정의 등)은 서버나 CLI가 시작할 때 바로 오류로 알려준다.

## 번역 (한국어 라벨)

번역은 다운로드 사본에 넣지 않고 `data/translations/`의 [Babelon](https://github.com/monarch-initiative/babelon) TSV로 관리한다. Babelon은 OBO 커뮤니티의 온톨로지 번역 표 형식으로, 한 행이 번역 하나다. 엑셀이나 구글 시트로 열어 고친 뒤 **탭 구분, UTF-8**로 저장하면 된다.

| 열 | 뜻 |
|---|---|
| `subject_id` | 번역 대상 (CURIE, 예: `bfo:BFO_0000040`) |
| `predicate_id` | 번역할 속성 (예: `rdfs:label`) |
| `source_value` | 원문 (현재 영어 라벨과 같아야 함, 테스트가 확인) |
| `translation_language` / `translation_value` | 번역 언어와 번역문 (비우면 건너뜀) |
| `translation_confidence` | 초안 단계에선 검토 우선순위 (0.4 = 논쟁 소지, 0.6 = 확인 필요, 0.9 = 명확) |
| `translation_type` | `MachineTranslated` = AI 초안, 검토가 끝나면 `HumanCurated` |
| `translator` / `translation_date` | 검토자(ORCID나 이름)와 날짜 |

```bash
uv run iof-gdb import data/translations/bfo-ko.babelon.tsv
```

각 행은 `rdfs:label "물질적 존재자"@ko`가 되어 `` `rdfs__label@ko` ``에 저장된다. 영어 기본 라벨(`rdfs__label`)은 그대로다. 검토가 끝난 행만 넣으려면 파이썬에서 `parse_babelon(..., curated_only=True)`를 쓴다.

| 파일 | 내용 | 상태 |
|---|---|---|
| [`bfo-ko.babelon.tsv`](data/translations/bfo-ko.babelon.tsv) | BFO 2020 클래스 37개 + 관계 64개 (전체) | AI 초안 (논쟁 용어는 추천안으로 잠정 채택) |
| [`iof-core-ko.babelon.tsv`](data/translations/iof-core-ko.babelon.tsv) | IOF Core 202603 클래스 91, 객체 속성 83, 데이터 속성 2, 어노테이션 속성 28, 개체 2 | AI 초안, 검토 중 |

```bash
uv run iof-gdb import data/translations/iof-core-ko.babelon.tsv
```

테스트가 번역 파일을 검사한다: 열 구성, 중복 행, 서로 다른 용어에 같은 번역어가 붙었는지, `source_value`가 현재 영어 라벨과 같은지(온톨로지 사본이 있을 때).

## DB 비우고 다시 넣기

저장 규칙이 바뀌었거나 처음부터 다시 넣고 싶을 때. **MCP로 넣은 인스턴스 데이터도 모두 지워지니** 필요하면 먼저 TTL로 남겨둔다.

```bash
uv run iof-gdb reset
```
노드·관계 수를 보여주고 확인을 받은 뒤 전부 지운다 (`--yes`면 묻지 않음). 제약조건은 남는다. MCP 도구로는 제공하지 않는다.

```bash
uv run iof-gdb import data/ontologies/Core.rdf --follow-imports
```

```bash
uv run iof-gdb import data/instances/example-pump.ttl
```

## 폐쇄망 (Windows, uv 없이 pip + 사내 PyPI 미러)

uv를 쓸 수 없는 폐쇄망 Windows PC(Python 3.12)에서는 pip와 사내 Nexus의 PyPI 프록시만으로 설치한다.

**venv를 두 개 쓴다.** `mcp-neo4j-cypher 0.6.0`은 fastmcp 2.13.3 / mcp 1.x가 필요하고, 이 프로젝트는 mcp 2.x를 쓴다. 같은 venv에 넣으면 다음처럼 충돌한다.

```
fastmcp 2.13.3 requires mcp!=1.21.1,<1.23,>=1.19.0, but you have mcp 2.3.0
```

uv 환경에서는 `uvx`가 이 격리를 대신해 줬는데, 여기서는 venv를 나눠 같은 효과를 낸다.

| 파일 | venv | 내용 |
|---|---|---|
| `requirements.txt` | `.venv` | 이 프로젝트의 의존성. `uv.lock`에서 `uv export --frozen --no-dev --no-emit-project`로 만든다 (해시 포함) |
| `requirements-cypher.txt` | `.venv-cypher` | mcp-neo4j-cypher와 의존성. 실제로 동작하는 `uvx mcp-neo4j-cypher@0.6.0` 환경(Python 3.12, Windows)에서 고정한 버전 |

```powershell
pip config set global.index-url https://<nexus>/repository/<pypi-proxy>/simple
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\pip install --no-deps -e .        # 빌드 백엔드 uv_build를 미러에서 받는다
py -3.12 -m venv .venv-cypher
.venv-cypher\Scripts\pip install -r requirements-cypher.txt
```

`-e`(editable)로 설치해야 `data/` 폴더 위치를 저장소 기준으로 찾는다. 명령은 `uv run iof-gdb …` 대신 `.venv\Scripts\iof-gdb …`로 실행한다.

`.env`에 cypher 서버 실행 파일을 적는다. 경로는 따옴표 없이 쓴다 (큰따옴표 안에서는 `\n`, `\t` 같은 조합이 이스케이프로 바뀔 수 있다). 이 값이 있으면 `iof-gdb-cypher`는 uvx 대신 이 파일을 직접 띄우고, 패키지 지정(`mcp-neo4j-cypher@…`) 인자는 무시한다. 접속 정보는 지금처럼 환경변수로만 넘긴다.

```
IOF_GDB_CYPHER_EXE=D:\work\iof-gdb\.venv-cypher\Scripts\mcp-neo4j-cypher.exe
```

`NEO4J_RESPONSE_TOKEN_LIMIT`은 설정하지 않는다. 설정하면 mcp-neo4j-cypher가 tiktoken으로 인코딩 파일을 인터넷에서 받으려 한다.

`.mcp.json`은 `uv run` 대신 venv의 실행 파일을 절대 경로로 가리킨다. 저장소의 `.mcp.json`은 uv용으로 그대로 두고, 폐쇄망 PC에서만 이렇게 바꿔 쓴다 (JSON이라 역슬래시를 두 번 쓴다).

```json
{
  "mcpServers": {
    "neo4j-cypher": {
      "command": "D:\\work\\iof-gdb\\.venv\\Scripts\\iof-gdb-cypher.exe",
      "args": ["--transport", "stdio"]
    },
    "iof-gdb": {
      "command": "D:\\work\\iof-gdb\\.venv\\Scripts\\iof-gdb-mcp.exe"
    }
  }
}
```

Open WebUI처럼 HTTP로 붙일 때는 5절의 명령을 같은 실행 파일로 바꿔 쓴다 (`.venv\Scripts\iof-gdb-mcp.exe --transport streamable-http …`, `.venv\Scripts\iof-gdb-cypher.exe --transport http …`).

**온톨로지**: 폐쇄망에서는 `fetch`가 동작하지 않는다. 인터넷이 되는 곳에서 `iof-gdb fetch-iof`로 받은 `data/ontologies/`(`catalog-v001.xml` 포함)를 통째로 반입한다. `import --follow-imports`는 카탈로그의 로컬 사본을 먼저 쓰므로 인터넷 없이 적재된다. 번역 TSV(`data/translations/`)는 저장소에 들어 있다.

**Neo4j**: Aura 대신 사내 서버를 쓴다.

```
NEO4J_URI=neo4j://<host>:7687
NEO4J_DATABASE=neo4j
```

Community 에디션은 사용자 DB가 `neo4j` 하나다. `get_neo4j_schema`가 쓰는 APOC Core는 기본으로 켜져 있지 않으니, Neo4j 설치 폴더의 `labs\apoc-*-core.jar`를 `plugins\`로 복사하고 서버를 다시 시작한다.

**의존성 버전을 바꾸면** 두 requirements 파일을 다시 만든다. `requirements.txt`는 `uv lock` 후 위 `uv export` 명령으로, `requirements-cypher.txt`는 바꾼 버전의 mcp-neo4j-cypher가 동작하는 환경에서 다시 고정한다.

## 알아둘 점

- **오래된 값**: 다시 임포트하면 같은 IRI의 속성은 새 값으로 바뀌지만, 원본에서 지워진 값이나 관계는 DB에서 지워지지 않는다. 원본에서 많이 바뀌었으면 위 절차로 다시 넣는다.
- **다중 값 판단 범위**: 같은 임포트 안의 값만 본다. 이미 DB에 단일 값이 있는 속성에 다른 임포트로 값을 넣으면 덮어쓴다 (여러 값이 필요한 속성은 프로필 `multivalued`에 넣는다).
- **배열 값 타입**: Neo4j 배열은 값 타입이 같아야 해서, 한 속성에 문자열과 숫자를 섞어 넣으면 임포트가 실패한다.
- **파생 구조**: `SUBCLASS_OF`와 `:BlankNode`는 임포트 때마다 전체를 다시 만든다. Cypher로 `rdfs__subClassOf`를 직접 고쳤다면 `uv run iof-gdb derive`로 다시 맞춘다.

## 개발

```bash
uv run pytest
```

```bash
uv run ruff check
```

- 단위 테스트는 메모리 Graph만 써서 DB가 필요 없다.
- 통합 테스트(`@pytest.mark.integration`)는 `NEO4J_*` 설정이 있고 실제로 접속될 때만 돈다. 접속이 안 되면 이유와 함께 skip된다. 테스트용 노드는 끝나면 지운다.

```bash
uv run pytest -m integration -rs
```

### Claude Code 웹에서 작업할 때

- Aura 접속 정보는 웹 환경의 환경변수로 등록한다. 저장소에는 넣지 않는다.
- 샌드박스 네트워크 설정에서 Aura(`*.databases.neo4j.io`, 포트 7687)와 IOF/BFO 다운로드 주소로 나가는 접속이 허용돼야 한다. 막혀 있으면 단위 테스트까지만 하고, 통합 테스트와 실제 적재는 로컬에서 한다.

## 라이선스

MIT. 내려받는 IOF·BFO 온톨로지는 각 배포처의 라이선스를 따른다.
