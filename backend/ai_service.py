import os
import asyncio
import base64
import tempfile
import uuid
import logging
from openai import OpenAI, AsyncOpenAI
from dotenv import load_dotenv
from schemas import StoryDraft

logger = logging.getLogger(__name__)

# ==========================================
# 0. 환경설정 및 클라이언트 준비
# ==========================================
load_dotenv() # .env 파일에서 API 키 불러오기

# GPT 텍스트 생성 및 DALL-E 이미지 편집용 (동기식 클라이언트)
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# TTS 음성 생성용 (비동기식 클라이언트)
aclient = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

TEXT_MODEL = os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini")
IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")  # DALL-E 3에서 최신 모델로 변경 권장


# ==========================================
# 1. [총괄 셰프] GPT-4o 스토리 & 퀴즈 대본 생성 (Structured Outputs)
# ==========================================
def generate_story_draft(child_name: str, age: int, personality: str, emotion: str, source_text: str) -> StoryDraft:
    logger.info("\n⏳ [GPT-4o] 동화 대본 및 캐릭터 설정 생성 중...")
    
    system_prompt = f"""
    당신은 {age}살 아이들의 마음을 읽어주는 최고의 맞춤형 동화 작가이자 교육 전문가입니다.
    아이의 이름은 '{child_name}'이고, 성향은 '{personality}'이며, 현재 기분은 '{emotion}' 상태입니다.
    
    이 아이를 달래주기 위해, 아래의 [학습 개념]을 자연스럽게 녹여낸 5장짜리 동화책 대본을 작성하세요.
    
    [학습 개념]
    {source_text}
    
    [작성 규칙]
    1. 주인공의 이름은 반드시 '{child_name}'으로 하세요.
    2. 총 5개의 씬(scene)으로 구성하세요. 
    3. 3번 씬과 5번 씬에는 반드시 [학습 개념]과 관련된 퀴즈(quiz)를 넣으세요. 나머지 씬의 quiz는 null로 비워두세요.
    4. 각 씬마다 DALL-E 3가 그림을 그릴 수 있도록, 'image_prompt'를 상세한 영어로 작성하세요. (수채화 풍의 따뜻한 동화책 스타일을 묘사할 것)
    5. 모든 동화 내용, 대사, 퀴즈는 반드시 '한국어'로 작성하세요. (단, DALL-E를 위한 image_prompt와 style_guide 등은 반드시 영어로 작성할 것)
    6. 일관된 그림 생성을 위해 'style_guide', 'character_bible', 'anchor_prompt'를 구체적인 영어로 작성하세요.
    """

    # GPT-4o 호출 (Structured Outputs 기능으로 JSON 틀 강제)
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-2024-08-06",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": "규격에 맞춰서 동화책 JSON 데이터를 생성해줘."}
        ],
        response_format=StoryDraft, 
    )

    story_draft = completion.choices[0].message.parsed
    logger.info(f"✅ [GPT-4o] 대본 생성 완료! 제목: {story_draft.title}")
    
    return story_draft


# ==========================================
# 2. [미술 감독] 캐릭터 시트(Anchor Image) 생성
# ==========================================
def generate_anchor_image(anchor_prompt: str, style_guide: str, character_bible: str) -> str:
    logger.info("🎨 [Anchor] 캐릭터 시트(기준 이미지) 생성 중...")
    
    full_prompt = f"""
    {style_guide}
    {character_bible}
    {anchor_prompt}
    Important: Create a character reference sheet showing the full body and face clearly.
    """
    
    try:
        params = {
            "model": IMAGE_MODEL,
            "prompt": full_prompt,
            "size": "1024x1024",
            "quality": "high",
            "n": 1,
        }
        
        # 최신 GPT Image 모델과 기존 DALL-E 모델 파라미터 분기
        if "gpt-image" in IMAGE_MODEL:
            params["output_format"] = "png"
        else:
            params["response_format"] = "b64_json"

        response = client.images.generate(**params)
        
        # 임시 파일로 저장 (OS 기본 임시 폴더 사용)
        item = response.data[0]
        b64 = item.b64_json if hasattr(item, "b64_json") and item.b64_json else item.b64
        image_data = base64.b64decode(b64)
        file_path = os.path.join(tempfile.gettempdir(), f"anchor_{uuid.uuid4().hex}.png")
        
        with open(file_path, "wb") as f:
            f.write(image_data)
            
        logger.info("✅ [Anchor] 캐릭터 시트 생성 완료!")
        return file_path
        
    except Exception as e:
        logger.error(f"❌ [Anchor] 생성 실패: {e}")
        return ""


# ==========================================
# 3. [미술팀] 일관성 있는 씬 이미지 생성 (Sequential Editing)
# ==========================================
def generate_scene_image_consistent(scene_no: int, scene_prompt: str, style_guide: str, character_bible: str, anchor_path: str, prev_image_path: str = None) -> str:
    logger.info(f"🎨 [{scene_no}번 씬] 일관성 있는 그림 그리는 중...")
    
    # 프롬프트 조합
    consistent_prompt = f"""
    {style_guide}
    {character_bible}
    Continuity rules: Keep the protagonist's face, hair, and outfit colors exactly the same as the reference image.
    match the watercolor texture and linework style.
    
    Scene Description:
    {scene_prompt}
    """
    
    try:
        # 안전한 파일 핸들링을 위해 리스트 준비
        image_files = [open(anchor_path, "rb")]
        try:
            if prev_image_path:
                image_files.append(open(prev_image_path, "rb"))
                
            # 모델 종류에 따라 파라미터 분기 처리
            params = {
                "model": IMAGE_MODEL,
                "image": image_files,
                "prompt": consistent_prompt,
                "n": 1,
                "size": "1024x1024",
                "quality": "high"
            }
            
            if "gpt-image" in IMAGE_MODEL:
                params["input_fidelity"] = "high" # 원본 캐릭터 유지율 증대
                params["output_format"] = "png"
            else:
                params["response_format"] = "b64_json"

            # 최신 다중 이미지 기반 edit 수행
            response = client.images.edit(**params)
            
            item = response.data[0]
            b64 = item.b64_json if hasattr(item, "b64_json") and item.b64_json else item.b64
            image_data = base64.b64decode(b64)
            file_path = os.path.join(tempfile.gettempdir(), f"scene_{scene_no}_{uuid.uuid4().hex}.png")
            
            with open(file_path, "wb") as f:
                f.write(image_data)
                
            logger.info(f"✅ [{scene_no}번 씬] 그림 완성!")
            return file_path
            
        finally:
            # 안전하게 열어둔 파일 객체들을 모두 닫습니다 (메모리 릭 및 권한 오류 방지)
            for f in image_files:
                try:
                    f.close()
                except Exception:
                    pass
                    
    except Exception as e:
        logger.error(f"❌ [{scene_no}번 씬] 그림 실패: {e}")
        return ""


# ==========================================
# 4. [음향팀] TTS 음성 생성 (비동기)
# ==========================================
async def generate_audio(text: str, scene_no: int):
    logger.info(f"🎵 [{scene_no}번 씬] 성우 녹음 중...")
    try:
        response = await aclient.audio.speech.create(
            model="gpt-4o-mini-tts", # 최신 고품질 효율 모델
            voice="alloy",  
            input=text
        )
        logger.info(f"✅ [{scene_no}번 씬] 녹음 완성!")
        return {"scene_no": scene_no, "type": "audio", "data": response.read()}
    except Exception as e:
        logger.error(f"❌ [{scene_no}번 씬] 녹음 실패: {e}")
        return {"scene_no": scene_no, "type": "audio", "data": None}


# ==========================================
# 5. [공장장] 순차적 그림 생성 & 비동기 음성 생성 혼합
# ==========================================
async def generate_all_media_sequential(story_draft: StoryDraft):
    print("\n🚀 [시퀀셜 공장 가동] 그림은 순서대로, 음성은 동시에 만듭니다!")
    
    # 1. Anchor Image 생성 (동기)
    anchor_path = generate_anchor_image(
        story_draft.anchor_prompt, 
        story_draft.style_guide, 
        story_draft.character_bible
    )
    
    media_results = []
    
    # 2. Scene Image 순차 생성 (Sequential)
    prev_image_path = None
    for scene in story_draft.scenes:
        # 그림 생성 (순차)
        img_path = generate_scene_image_consistent(
            scene_no=scene.scene_no,
            scene_prompt=scene.image_prompt,
            style_guide=story_draft.style_guide,
            character_bible=story_draft.character_bible,
            anchor_path=anchor_path,
            prev_image_path=prev_image_path
        )
        
        # 파일 경로를 결과에 담음 (나중에 업로드할 때 읽음)
        if img_path:
            with open(img_path, "rb") as f:
                img_bytes = f.read()
            media_results.append({"scene_no": scene.scene_no, "type": "image", "data": img_bytes})
            prev_image_path = img_path # 다음 씬을 위해 경로 업데이트
        else:
            media_results.append({"scene_no": scene.scene_no, "type": "image", "data": None})

    # 3. Audio 생성 (병렬 - 변화 없음)
    audio_tasks = []
    for scene in story_draft.scenes:
        # 내레이션 대사 사용
        audio_tasks.append(generate_audio(scene.text, scene.scene_no))
        
    audio_results = await asyncio.gather(*audio_tasks)
    media_results.extend(audio_results)
    
    print("🎉 [공장 완료] 모든 미디어 파일 생성 끝!\n")
    return media_results