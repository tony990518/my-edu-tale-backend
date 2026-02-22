# Edu-Tale Backend Documentation

이 문서는 `Edu-Tale` 프로젝트의 **Backend** 시스템 구조와 로직을 상세하게 설명합니다.  
Backend는 FastAPI를 기반으로 구축되었으며, OpenAI(GPT-4o, 최신 GPT Image 모델/DALL-E 3, 최신 TTS)와 Supabase(Database, Storage)를 연동하여 맞춤형 멀티미디어 동화책을 생성하는 핵심 엔진 역할을 수행합니다.

## 1. 프로젝트 개요

-   **프레임워크**: FastAPI (Python 3.10+)
-   **용도**: 사용자 맞춤형 동화 생성 API 서버
-   **주요 기능**:
    -   OpenAI GPT-4o를 활용한 동화 대본, 이미지 프롬프트 및 퀴즈 생성 (Structured Outputs)
    -   최신 `gpt-image-1.5` 모델을 활용한 '이전 씬 유지(일관성)' 기법의 동화 삽화 순차 생성
    -   OpenAI 최신 TTS(`gpt-4o-mini-tts`)를 활용한 고음질 내레이션 음성 생성 (병렬 처리)
    -   Supabase Storage를 활용한 생성된 미디어 파일(바이트 스트림) 다이렉트 업로드
    -   Supabase Database를 활용한 교재 데이터 조회 및 최종 생성물 영구 저장

## 2. 파일 구조 (Directory Structure)

`backend/` 폴더 내의 주요 파일들은 다음과 같은 역할을 수행합니다.

| 파일명 | 역할 및 설명 |
| :--- | :--- |
| **`main.py`** | **API 진입점 (Entry Point)**. 클라이언트 요청을 받아 전체 생성 프로세스를 지휘하는 컨트롤 타워입니다. |
| **`schemas.py`** | **데이터 규격 (Pydantic Models)**. 프론트엔드와 주고받는 데이터 및 AI가 생성해야 할 데이터의 형식을 정의합니다. |
| **`ai_service.py`** | **AI 로직 (OpenAI Service)**. 최신 GPT 모델들을 호출하여 창작물(스토리, 앵커 이미지, 씬 이미지, 음성)을 생성합니다. |
| **`db_service.py`** | **DB 로직 (Supabase Service)**. 교재 데이터 조회, 바이너리 파일 업로드(Storage), 최종 결과 저장(Database)을 담당합니다. |
| **`.env`** | **환경 변수 파일**. `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_API` 및 모델 환경변수(`OPENAI_TEXT_MODEL`, `OPENAI_IMAGE_MODEL`)를 관리합니다. |

---

## 3. 상세 컴포넌트 분석

### 3.1. `main.py` (Main Controller)

FastAPI 애플리케이션의 핵심 파일로, 다음의 주요 엔드포인트들을 처리합니다.

-   **`POST /generate`**: 전체 생성 프로세스를 지휘하는 핵심 컨트롤 타워입니다.
-   **`GET /curriculums`**: 진도 선택 화면 구성을 위한 전체 교재 목록을 반환합니다.
-   **`GET /stories/{story_id}`**: 특정 ID의 동화책 데이터를 조회합니다.

-   **설정**: CORS 미들웨어가 적용되어 있어 외부 프론트엔드에서의 요청을 안전하게 허용합니다.
-   **핵심 로직 (`generate_story`)**:
    1.  **Step 1. 교재 조회**: `db_service`를 통해 `stage_code`에 맞는 학습 내용을 가져옵니다.
    2.  **Step 2. 대본 생성**: `ai_service`를 통해 GPT-4o에게 스토리, 스타일 가이드, 캐릭터 설정 및 퀴즈를 작성시킵니다.
    3.  **Step 3. 미디어 생성 (순차+병렬 하이브리드)**: 
        - 삽화(이미지)는 일관성 유지를 위해 **순차적**으로 생성합니다.
        - 음성(오디오)은 처리 속도 향상을 위해 `asyncio`로 **병렬** 생성합니다.
    4.  **Step 4. 업로드**: 로컬 찌꺼기 파일 없이 확보한 메모리 상의 바이트(Bytes) 데이터를 Supabase Storage에 다이렉트로 업로드하고 영구 URL을 획득합니다.
    5.  **Step 5. 최종 조립 및 DB 저장**: 모든 URL과 대본 정보를 조합하여 `stories` 테이블에 JSON 형태로 저장합니다.
    6.  **Step 6. 응답**: 프론트엔드에 최종 완성된 JSON 데이터를 반환합니다.

### 3.2. `schemas.py` (Data Models)

데이터의 유효성을 검사하고 구조를 정의하는 Pydantic 모델들입니다.

-   **`GenerateRequest`**: 프론트엔드 요청 바디 (아이 정보, 진도 코드 등).
-   **`QuizSchema`**: GPT가 생성할 퀴즈 정보 (유형, 질문, 정답, 피드백).
-   **`SceneSchema`**: 각 장면(Scene)의 구성 요소 (단일 통함 텍스트, 이미지 프롬프트 등).
-   **`StoryDraft`**: GPT-4o가 생성하는 전체 스토리 초안 구조. 일관성을 위한 전역 필드(`style_guide`, `character_bible`, `anchor_prompt`)와 5개의 `scenes` 리스트를 포함합니다.

### 3.3. `ai_service.py` (AI Service Layer)

OpenAI API를 래핑하여 구체적인 생성 작업을 수행합니다. 운영체제가 지원하는 `tempfile` 모듈을 사용하여 임시 파일을 안전하게 관리합니다.

-   **`generate_story_draft`**:
    -   **Model**: `gpt-4o-2024-08-06` 
    -   **특징**: `response_format`을 사용하여 `StoryDraft` 스키마에 맞는 JSON 출력을 100% 강제합니다 (Structured Outputs).
-   **`generate_anchor_image`**:
    -   주인공의 기준점인 레퍼런스 캐릭터 시트를 가장 먼저 생성합니다.
    -   Base64 포맷으로 그림을 받아 안전하게 임시 파일(`/tmp` 등)로 저장합니다.
-   **`generate_scene_image_consistent` (핵심 편집 로직)**:
    -   **Model**: 설정된 `IMAGE_MODEL` (권장: `gpt-image-1.5`)
    -   **특징**: 단순 이미지를 Generate 하는 것이 아니라, OpenAI의 `images.edit` 기능을 활용합니다. 입력(Input) 배열에 '생성해둔 Anchor 이미지'와 '직전 씬의 결과물 이미지'를 중첩으로 넘겨주어 연속적인 장면 구도와 얼굴 일관성을 강력하게 유지합니다.
-   **`generate_audio` (Async)**:
    -   **Model**: `gpt-4o-mini-tts` (최신 고품질 효율 모델)
    -   **Voice**: `alloy`
-   **`generate_all_media_sequential`**:
    -   스토리 일관성을 위해 이미지 작업과 오디오 작업을 분리/조율하여 실행하는 최종 팩토리 함수입니다.

### 3.4. `db_service.py` (Database Service Layer)

Supabase(PostgreSQL + Storage)와의 통신을 전담합니다.

-   **`get_curriculum`**: `curriculums` 테이블에서 `stage_code`로 교재 정보를 조회합니다.
-   **`upload_to_supabase`**:
    -   파일의 **바이트(Bytes) 데이터** 자체를 받아 `edu-tale-assets` 스토리지 버킷에 즉시 업로드합니다.
    -   업로드 후 즉시 접근 가능한 `public_url`을 반환합니다.
-   *참고: `save_image_from_url` (기존 DALL-E 임시 URL 다운로드 처리 함수)는 현재 `b64` 연동 방식으로 파이프라인이 업그레이드됨에 따라 내부적으로 거의 사용하지 않는 레거시 함수가 되었습니다.*
-   **`save_final_story`**:
    -   조립이 완료된 완벽한 동화책 JSON 데이터를 `stories` 테이블에 INSERT하고, 성공 시 생성된 `id`를 반환합니다.

## 4. 데이터 흐름 (Data Flow)

1.  **User Request** -> `GenerateRequest` (JSON)
2.  **DB Lookup** -> Curriculum Source Text
3.  **GPT Generation** -> `StoryDraft` (Structured JSON)
4.  **Hybrid Generation** ->
    -   Anchor Prompt -> **GPT Image Generate** -> Base64 -> 내부 임시 파일
    -   Scene Prompts -> **GPT Image Edit (with Anchor & Prev Image)** -> Base64 -> 임시 파일 -> **완성된 Image Bytes 반환**
    -   Text -> **TTS API (Parallel)** -> **완성된 Audio Bytes 반환**
5.  **Storage Upload** ->
    -   메모리 상의 Image Bytes -> **Supabase Storage** -> Permanent Image URL
    -   메모리 상의 Audio Bytes -> **Supabase Storage** -> Permanent Audio URL
6.  **Final Assembly** -> `StoryDraft` 딕셔너리에 Permanent URLs 주입
7.  **DB Save** -> `stories` Table 에 최종 JSON Insert
8.  **API Response** -> Final Story JSON (Frontend Rendering)
