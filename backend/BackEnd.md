# Edu-Tale Backend Documentation

이 문서는 `Edu-Tale` 프로젝트의 **Backend** 시스템 구조와 로직을 상세하게 설명합니다.  
Backend는 FastAPI를 기반으로 구축되었으며, OpenAI(GPT-4o, DALL-E 3, TTS)와 Supabase(Database, Storage)를 연동하여 맞춤형 멀티미디어 동화책을 생성하는 핵심 엔진 역할을 수행합니다.

## 1. 프로젝트 개요

-   **프레임워크**: FastAPI (Python 3.10+)
-   **용도**: 사용자 맞춤형 동화 생성 API 서버
-   **주요 기능**:
    -   OpenAI GPT-4o를 활용한 동화 대본 및 퀴즈 생성
    -   DALL-E 3를 활용한 동화 삽화 생성 (병렬 처리)
    -   OpenAI TTS (Text-to-Speech)를 활용한 나레이션 음성 생성 (병렬 처리)
    -   Supabase를 활용한 교재 데이터 조회 및 생성물 영구 저장

## 2. 파일 구조 (Directory Structure)

`backend/` 폴더 내의 주요 파일들은 다음과 같은 역할을 수행합니다.

| 파일명 | 역할 및 설명 |
| :--- | :--- |
| **`main.py`** | **API 진입점 (Entry Point)**. 클라이언트 요청을 받아 전체 생성 프로세스를 지휘하는 컨트롤 타워입니다. |
| **`schemas.py`** | **데이터 규격 (Pydantic Models)**. 프론트엔드와 주고받는 데이터 및 AI가 생성해야 할 데이터의 형식을 정의합니다. |
| **`ai_service.py`** | **AI 로직 (OpenAI Service)**. GPT-4o, DALL-E 3, TTS 모델을 호출하여 창작물을 생성합니다. |
| **`db_service.py`** | **DB 로직 (Supabase Service)**. 교재 데이터 조회, 파일 업로드(Storage), 최종 결과 저장(Database)을 담당합니다. |
| **`.env`** | **환경 변수 파일**. `OPENAI_API_KEY`, `SUPABASE_URL`, `SUPABASE_API` 등 민감한 정보를 관리합니다. |

---

## 3. 상세 컴포넌트 분석

### 3.1. `main.py` (Main Controller)

FastAPI 애플리케이션의 핵심 파일로, 다음의 주요 엔드포인트들을 처리합니다.

-   **`POST /generate`**: 전체 생성 프로세스를 지휘하는 핵심 컨트롤 타워입니다.
-   **`GET /curriculums`**: 진도 선택 화면 구성을 위한 전체 교재 목록을 반환합니다.
-   **`GET /stories/{story_id}`**: 특정 ID의 동화책 데이터를 조회합니다.

-   **설정**: CORS 미들웨어가 적용되어 있어 프론트엔드(localhost:3000)에서의 요청을 허용합니다.
-   **핵심 로직 (`generate_story`)**:
    1.  **Step 1. 교재 조회**: `db_service`를 통해 `stage_code`에 맞는 학습 내용을 가져옵니다.
    2.  **Step 2. 대본 생성**: `ai_service`를 통해 GPT-4o에게 스토리와 퀴즈를 작성시킵니다.
    3.  **Step 3. 미디어 생성 (병렬)**: 5개 장면의 그림과 음성을 `asyncio`를 사용하여 동시에 생성합니다.
    4.  **Step 4. 업로드**: 생성된 미디어 파일들을 Supabase Storage에 업로드하고 영구 URL을 획득합니다.
    5.  **Step 5. 최종 조립 및 DB 저장**: 모든 정보를 조합하여 `stories` 테이블에 저장합니다.
    6.  **Step 6. 응답**: 프론트엔드에 최종 JSON 데이터를 반환합니다.

### 3.2. `schemas.py` (Data Models)

데이터의 유효성을 검사하고 구조를 정의하는 Pydantic 모델들입니다.

-   **`GenerateRequest`**: 프론트엔드 요청 바디 (아이 정보, 진도 코드 등).
-   **`QuizSchema`**: GPT가 생성할 퀴즈 정보 (유형, 질문, 정답, 피드백).
-   **`SceneSchema`**: 각 장면(Scene)의 구성 요소 (텍스트, 이미지 프롬프트, 퀴즈 포함).
-   **`StoryDraft`**: GPT-4o가 생성하는 전체 스토리 초안 구조 (제목, 요약, 5개의 Scene 리스트).

### 3.3. `ai_service.py` (AI Service Layer)

OpenAI API를 래핑하여 구체적인 생성 작업을 수행합니다.

-   **`generate_story_draft`**:
    -   **Model**: `gpt-4o-2024-08-06`
    -   **특징**: `response_format`을 사용하여 `StoryDraft` 스키마에 딱 맞는 JSON 출력을 강제합니다 (Structured Outputs).
    -   **Prompt**: 아이의 특성과 학습 개념을 반영하여 5장 분량의 동화를 작성하도록 지시합니다.
    -   **Rule**: 3번 씬과 5번 씬에는 반드시 학습 개념과 관련된 퀴즈를 포함하도록 설계되었습니다.
-   **`generate_image`** (Async):
    -   **Model**: `dall-e-3`
    -   **Size**: 1024x1024
    -   **Prompt**: GPT가 작성한 상세한 영어 프롬프트를 사용합니다.
-   **`generate_audio`** (Async):
    -   **Model**: `tts-1` (속도 최적화)
    -   **Voice**: `nova` (여성 목소리)
-   **`generate_all_media_parallel`**:
    -   `asyncio.gather`를 사용하여 그림 5장 + 음성 5개 (총 10개 작업)를 **동시에 실행**시켜 전체 대기 시간을 획기적으로 단축합니다.

### 3.4. `db_service.py` (Database Service Layer)

Supabase(PostgreSQL + Storage)와의 통신을 전담합니다.

-   **`get_curriculum`**: `curriculums` 테이블에서 `stage_code`로 교재 정보를 조회합니다.
-   **`upload_to_supabase`**:
    -   파일(bytes)을 받아 `edu-tale-assets` 스토리지 버킷에 업로드합니다.
    -   파일명 충돌 방지를 위해 `uuid`를 사용합니다.
    -   업로드 후 접근 가능한 `public_url`을 반환합니다.
-   **`save_image_from_url`**:
    -   DALL-E가 반환한 임시 URL(1~2시간 유효)에서 이미지를 다운로드한 후, 다시 Supabase에 업로드하여 영구적인 URL로 변환합니다.
-   **`save_final_story`**:
    -   완성된 동화책 데이터를 `stories` 테이블에 INSERT하고, 생성된 `id`를 반환합니다.

## 4. 데이터 흐름 (Data Flow)

1.  **User Request** -> `GenerateRequest` (JSON)
2.  **DB Lookup** -> Curriculum Source Text
3.  **GPT Generation** -> `StoryDraft` (JSON: Text + Image Prompts)
4.  **Parallel Generation** ->
    -   Image Prompts -> **DALL-E 3** -> Temporary Image URLs
    -   Text -> **TTS API** -> Audio Bytes
5.  **Storage Upload** ->
    -   Temporary Image URL -> Download -> **Supabase Storage** -> Permanent Image URL
    -   Audio Bytes -> **Supabase Storage** -> Permanent Audio URL
6.  **Final Assembly** -> `StoryDraft` + Permanent URLs
7.  **DB Save** -> `stories` Table Insert
8.  **API Response** -> Final Story JSON (Frontend Rendering)
