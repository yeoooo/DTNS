# DTNS

DTNS는 AI 기반 주간 엔지니어링 뉴스레터 CLI입니다.

다양한 엔지니어링 뉴스 소스를 수집한 뒤 AI를 활용하여 기술 태깅과 주제 분류를 수행하고, 주간 기술 트렌드를 분석하여 한국어 뉴스레터를 생성한 후 Discord Webhook으로 발행합니다.

이 프로젝트는 오픈소스 유지보수성을 최우선으로 설계되었습니다. 전체 기능을 구현하기 전에 아키텍처, 데이터 계약(Contract), 모듈 경계를 먼저 정의하는 것을 원칙으로 합니다.

---

# 뉴스레터 주제

DTNS는 서로 독립적으로 운영되는 세 가지 뉴스레터를 제공합니다.

| 주제 | 설명 |
|------|------|
| `technology` | 소프트웨어 엔지니어링 전반의 기술 동향 및 생태계 트렌드 |
| `backend` | 백엔드 개발, 인프라, API, 데이터 시스템 및 운영 기술 |
| `qa` | QA, 테스트 자동화, 품질 관리(Quality Engineering) 관련 기술 |

하나의 기사는 여러 주제에 동시에 포함될 수 있습니다.

예를 들어,

- OpenTelemetry 릴리스는 `technology`와 `backend`
- Testcontainers 업데이트는 `backend`와 `qa`

처럼 다중 분류(Multi-label Classification)를 지원합니다.

---

# 전체 처리 과정

```text
Collector
  ↓
articles.json

Preprocessor
  ↓
normalized_articles.json

Tagger Agent
  ↓
tagged_articles.json

Classifier
  ├─ technology_articles.json
  ├─ backend_articles.json
  └─ qa_articles.json

Trend Agent (주제별)
  ├─ technology_trends.json
  ├─ backend_trends.json
  └─ qa_trends.json

Editor Agent (주제별)
  ├─ technology_newsletter.md
  ├─ backend_newsletter.md
  └─ qa_newsletter.md

Publisher
  ↓
Discord Webhook 발행
```

각 단계는 파일을 통해서만 데이터를 주고받습니다.

각 모듈은 다른 모듈의 내부 구현을 알 필요가 없으며, 계약된 입력과 출력 형식만을 사용합니다.

---

# 모듈별 역할

## Collector

뉴스 및 기술 블로그에서 기사 메타데이터를 수집합니다.

- AI 사용하지 않음

---

## Preprocessor

수집한 데이터를 후처리합니다.

- 데이터 정규화
- 중복 제거
- URL 정리
- Stable ID 생성
- 데이터 검증

AI를 사용하지 않습니다.

---

## Tagger Agent

Gemini를 이용하여 기사의 기술 정보를 분석합니다.

예를 들어,

- 기술 스택
- 프레임워크
- 도메인
- 기술 태그

등을 추출하여 JSON으로 저장합니다.

---

## Classifier

Tagger 결과를 기반으로

- Technology
- Backend
- QA

세 가지 뉴스레터 주제로 결정론적(Multi-label) 분류를 수행합니다.

AI를 사용하지 않습니다.

---

## Trend Agent

각 뉴스레터 주제별로

- 반복적으로 등장하는 기술
- 주요 기술 흐름
- 여러 기사를 하나의 트렌드로 묶는 작업

을 수행합니다.

결과는 JSON 형태로 저장됩니다.

---

## Editor Agent

Trend Agent의 결과를 기반으로

사람이 읽기 쉬운 한국어 Markdown 뉴스레터를 작성합니다.

---

## Publisher

완성된 Markdown 뉴스레터를

각 Discord Webhook으로 발행합니다.

AI를 사용하지 않습니다.

---

# 프로젝트 구조

```text
AGENTS.md
docs/
  architecture.md
  decisions.md
  contracts/

src/dtns/
  cli.py

  collectors/
  preprocessors/
  classifier/

  agents/
    tagger/
    trend/
    editor/

  publisher/

  contracts/
```

새로운 기능을 구현할 때는 반드시

- AGENTS.md
- docs/architecture.md
- docs/decisions.md

를 기준으로 개발합니다.

---

# 데이터 계약(Contracts)

모든 데이터 형식은 `docs/contracts/`에서 관리합니다.

- articles.schema.json
- normalized_articles.schema.json
- tagged_articles.schema.json
- topic_articles.schema.json
- trends.schema.json
- newsletter.md

모든 모듈은 계약된 데이터 형식만을 사용하여 통신합니다.

---

# CLI 명세

```bash
newsletter collect
newsletter preprocess
newsletter tag
newsletter classify

newsletter trend --topic technology
newsletter trend --topic backend
newsletter trend --topic qa

newsletter edit --topic technology
newsletter edit --topic backend
newsletter edit --topic qa

newsletter publish --topic technology
newsletter publish --topic backend
newsletter publish --topic qa

newsletter run-all
```

현재 CLI는 프로젝트의 아키텍처 계약(Architectural Contract)입니다.

초기에는 각 명령이 Stub 형태로 존재하더라도, 이후 개발 과정에서 하나의 책임만 수행하는 독립적인 구현으로 대체됩니다.

---

# 개발 환경

```bash
uv sync --extra dev

cp .env.example .env
```

## AI 설정

AI Provider

- Gemini API (`google-genai`)

기본 모델

```
gemini-2.0-flash
```

필수 환경 변수

```text
GEMINI_API_KEY=

DISCORD_WEBHOOK_TECHNOLOGY=
DISCORD_WEBHOOK_BACKEND=
DISCORD_WEBHOOK_QA=
```

---

# 개발

테스트 실행

```bash
uv run pytest
```

로컬 테스트

```bash
uv sync --extra dev

newsletter --help

newsletter --data-dir data preprocess

newsletter --data-dir data classify
```

다음 명령은 Gemini API Key가 필요합니다.

- tag
- trend
- edit

publish 명령은 선택한 뉴스레터 주제에 대응하는 Discord Webhook 환경 변수가 필요합니다.

---

# 자동화

DTNS는

- GitHub Actions Cron
- 로컬 스케줄러

환경에서 실행할 수 있도록 설계되었습니다.

초기 운영 환경은 GitHub Actions Cron을 목표로 합니다.

CLI 기반으로 실행되므로 애플리케이션은 상태를 저장하지 않는(Stateless) 구조를 유지하며, GitHub Actions에서 주기적으로 실행하는 것만으로 전체 뉴스레터 생성 및 발행 과정을 수행할 수 있습니다.
