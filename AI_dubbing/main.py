import os
import shutil
import subprocess
import requests
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.responses import FileResponse, JSONResponse
from pydub import AudioSegment
from dotenv import load_dotenv
from pydantic import BaseModel
from typing import List

os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ["PYTHONWARNINGS"] = "ignore"

load_dotenv()

from translating import process_audio_gemini

app = FastAPI(title="AI Dubbing Studio API")

TEMP_DIR = "temp_files"
OUTPUT_DIR = "output_videos"

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

VIDEO_EXTENSIONS = {"mp4", "mkv", "avi", "mov", "flv", "webm", "wmv"}

class ChunkItem(BaseModel):
    chunk_index: int
    audio_path: str = ""
    video_path: str
    duration_ms: int
    global_start_sec: float = 0.0

class TTSPayload(BaseModel):
    chunk_index: int
    text_en: str
    text_vi: str
    start: float
    end: float

# =====================================================================
# GIAI ĐOẠN 1: UPLOAD & CHUNKING (CÓ TÙY CHỈNH THỜI LƯỢNG)
# =====================================================================
@app.post("/api/v1/upload-chunking")
async def upload_and_chunking(
    file: UploadFile = File(...),
    chunk_length_sec: int = Form(300) 
):
    file_extension = os.path.splitext(file.filename)[1].replace(".", "").lower()
    if file_extension not in VIDEO_EXTENSIONS:
        return JSONResponse(status_code=400, content={"error": "Định dạng file không hỗ trợ!"})

    original_video_name = file.filename
    temp_video_path = os.path.join(TEMP_DIR, original_video_name)
    
    with open(temp_video_path, 'wb') as f:
        shutil.copyfileobj(file.file, f)

    try:
        sound = AudioSegment.from_file(temp_video_path)
        duration_ms = len(sound)
        chunk_length_ms = chunk_length_sec * 1000
        
        created_chunks = []
        start_ms = 0
        chunk_idx = 0
        
        while start_ms < duration_ms:
            end_ms = min(start_ms + chunk_length_ms, duration_ms)
            global_start_sec = start_ms / 1000.0
            
            chunk_audio_path = os.path.join(TEMP_DIR, f"audio_chunk_{chunk_idx}.mp3")
            sound[start_ms:end_ms].export(chunk_audio_path, format="mp3")
            
            chunk_video_path = os.path.join(TEMP_DIR, f"video_chunk_{chunk_idx}.mp4")
            cmd_cut = [
                'ffmpeg', '-y', '-ss', str(global_start_sec), '-to', str(end_ms / 1000.0),
                '-i', temp_video_path, '-c', 'copy', chunk_video_path
            ]
            subprocess.run(cmd_cut, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            
            created_chunks.append({
                "chunk_index": chunk_idx,
                "audio_path": chunk_audio_path,
                "video_path": chunk_video_path,
                "duration_ms": end_ms - start_ms,
                "global_start_sec": global_start_sec
            })
            
            start_ms += chunk_length_ms
            chunk_idx += 1

        return {
            "original_video": original_video_name,
            "total_chunks": chunk_idx,
            "chunks": created_chunks
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Lỗi Giai đoạn 1: {str(e)}"})


# =====================================================================
# GIAI ĐOẠN 2: WHISPER -> TRANSLATE (XỬ LÝ ĐỘC LẬP TỪNG CHUNK)
# =====================================================================
@app.post("/api/v1/whisper-translate")
def whisper_translate(chunks_info: List[ChunkItem], original_video_name: str):
    if not chunks_info:
        return JSONResponse(status_code=400, content={"error": "Dữ liệu chunks trống!"})

    manifest_filename = f"manifest_{original_video_name}.log"
    manifest_filepath = os.path.join(TEMP_DIR, manifest_filename)

    target_chunks = [c.chunk_index for c in chunks_info]
    existing_lines = []
    
    if os.path.exists(manifest_filepath):
        with open(manifest_filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()
            if len(lines) > 1:
                for line in lines[1:]:
                    parts = line.split(" | ")
                    if len(parts) >= 5 and int(parts[0]) not in target_chunks:
                        existing_lines.append(line)

    # 2. Tạo lại file log, nhồi header và các dòng của Chunk khác vào
    with open(manifest_filepath, "w", encoding="utf-8") as f_log:
        f_log.write("CHUNK_INDEX | TEXT_EN | TEXT_VI | ZALO_AUDIO_URL | GLOBAL_START_END\n")
        for line in existing_lines:
            f_log.write(line)

    # 3. Chạy Whisper & Dịch cho Chunk được chỉ định, rồi append vào cuối
    try:
        for item in chunks_info:
            raw_segments = process_audio_gemini(item.audio_path)
            
            if raw_segments:
                with open(manifest_filepath, "a", encoding="utf-8") as f_log:
                    for seg in raw_segments:
                        global_start = seg["start"] + item.global_start_sec
                        global_end = seg["end"] + item.global_start_sec
                        text_en = seg.get("text_en", "N/A").replace("\n", " ").strip()
                        text_vi = seg.get("text_vi", "").replace("\n", " ").strip()
                        
                        f_log.write(f"{item.chunk_index} | {text_en} | {text_vi} | N/A | {global_start}-{global_end}\n")

        return {"message": "Giai đoạn 2 hoàn thành.", "manifest_file": manifest_filename}
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Lỗi Giai đoạn 2: {str(e)}"})


# =====================================================================
# API SINH GIỌNG ĐỌC ZALO VÀ LƯU LOG
# =====================================================================
@app.post("/api/v1/generate-zalo-manifest")
def generate_zalo_manifest(payload: List[TTSPayload], original_video_name: str):
    manifest_filename = f"manifest_{original_video_name}.log"
    manifest_filepath = os.path.join(TEMP_DIR, manifest_filename)

    existing_data = []
    if os.path.exists(manifest_filepath):
        with open(manifest_filepath, "r", encoding="utf-8") as f:
            existing_data = f.readlines()[1:]

    with open(manifest_filepath, "w", encoding="utf-8") as f_log:
        f_log.write("CHUNK_INDEX | TEXT_EN | TEXT_VI | ZALO_AUDIO_URL | GLOBAL_START_END\n")

    ZALO_TTS_URL = "https://api.zalo.ai/v1/tts/synthesize"
    ZALO_API_KEY = os.getenv("ZALO_API_KEY", "")
    success_count = 0

    with open(manifest_filepath, "a", encoding="utf-8") as f_log:
        for item in payload:
            text_vi_clean = item.text_vi.replace("\n", " ").strip()
            zalo_url = "N/A"

            if text_vi_clean:
                try:
                    payload_zalo = {
                        "input": text_vi_clean,
                        "speaker_id": str(os.getenv("ZALO_SPEAKER_ID", "4")),
                        "encode_type": "1"
                    }
                    headers = {"apikey": ZALO_API_KEY, "Content-Type": "application/x-www-form-urlencoded"}
                    resp = requests.post(ZALO_TTS_URL, data=payload_zalo, headers=headers, timeout=10)
                    
                    result = resp.json()
                    if resp.status_code == 200 and result.get("error_code") == 0:
                        zalo_url = result["data"]["url"]
                        success_count += 1
                except Exception as e:
                    print(f"[Zalo Lỗi] {str(e)}")

            f_log.write(f"{item.chunk_index} | {item.text_en} | {text_vi_clean} | {zalo_url} | {item.start}-{item.end}\n")

    return {"message": "Đã sinh xong giọng đọc!"}


# =====================================================================
# GIAI ĐOẠN 3: GHÉP NỐI VIDEO
# =====================================================================
@app.post("/api/v1/merge-dubbing")
def merge_and_output(payload: List[ChunkItem], original_video_name: str):
    temp_video_path = os.path.join(TEMP_DIR, original_video_name)
    manifest_filepath = os.path.join(TEMP_DIR, f"manifest_{original_video_name}.log")
    dubbed_video_chunks = []
    
    manifest_data = {}
    if os.path.exists(manifest_filepath):
        with open(manifest_filepath, "r", encoding="utf-8") as f:
            lines = f.readlines()[1:]
            for line in lines:
                parts = line.split(" | ")
                if len(parts) >= 5:
                    c_idx = int(parts[0])
                    if c_idx not in manifest_data:
                        manifest_data[c_idx] = []
                    manifest_data[c_idx].append({
                        "text_vi": parts[2],
                        "zalo_url": parts[3].strip(),
                        "start": float(parts[4].strip().split("-")[0])
                    })

    try:
        for item in payload:
            idx = item.chunk_index
            file_pure_name = os.path.basename(item.video_path)
            v_path = os.path.abspath(os.path.join(TEMP_DIR, file_pure_name))
            c_len = item.duration_ms
            
            dubbed_chunk_video_path = os.path.abspath(os.path.join(TEMP_DIR, f"dubbed_chunk_{idx}.mp4"))

            if os.path.exists(dubbed_chunk_video_path) and not os.path.exists(v_path):
                dubbed_video_chunks.append(dubbed_chunk_video_path)
                continue

            if not os.path.exists(v_path):
                continue

            segments = manifest_data.get(idx, [])
            if not segments:
                cmd_mute_chunk = ['ffmpeg', '-y', '-i', v_path, '-an', '-c:v', 'copy', dubbed_chunk_video_path]
                subprocess.run(cmd_mute_chunk, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                dubbed_video_chunks.append(dubbed_chunk_video_path)
                continue

            chunk_speech = AudioSegment.silent(duration=c_len)
            has_valid_speech = False
            global_start_of_this_chunk = (idx * item.duration_ms) / 1000.0
            
            for j, seg in enumerate(segments):
                zalo_url = seg["zalo_url"]
                if zalo_url == "N/A": continue
                
                seg_start_ms = round((seg["start"] - global_start_of_this_chunk) * 1000)
                if -1000 < seg_start_ms < 0: seg_start_ms = 0
                if seg_start_ms < 0 or seg_start_ms >= c_len: continue
                
                seg_audio_path = os.path.join(TEMP_DIR, f"seg_{idx}_{j}.mp3")
                try:
                    audio_resp = requests.get(zalo_url, timeout=15)
                    if audio_resp.status_code == 200:
                        with open(seg_audio_path, "wb") as f_aud:
                            f_aud.write(audio_resp.content)
                        
                        if os.path.exists(seg_audio_path) and os.path.getsize(seg_audio_path) > 0:
                            seg_sound = AudioSegment.from_file(seg_audio_path)
                            chunk_speech = chunk_speech.overlay(seg_sound, position=seg_start_ms)
                            has_valid_speech = True
                        if os.path.exists(seg_audio_path): os.remove(seg_audio_path)
                except Exception as e_download:
                    print(f"[Merge Lỗi] {str(e_download)}")

            if not has_valid_speech:
                cmd_mute_chunk = ['ffmpeg', '-y', '-i', v_path, '-an', '-c:v', 'copy', dubbed_chunk_video_path]
                subprocess.run(cmd_mute_chunk, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                dubbed_video_chunks.append(dubbed_chunk_video_path)
                continue

            final_chunk_audio_path = os.path.join(TEMP_DIR, f"speech_chunk_{idx}.mp3")
            chunk_speech.export(final_chunk_audio_path, format="mp3")
            
            cmd_merge_chunk = [
                'ffmpeg', '-y', '-i', v_path, '-i', final_chunk_audio_path,
                '-map', '0:v:0', '-map', '1:a:0',
                '-c:v', 'copy', '-c:a', 'aac', '-ar', '44100', '-ac', '2',
                dubbed_chunk_video_path
            ]
            subprocess.run(cmd_merge_chunk, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            dubbed_video_chunks.append(dubbed_chunk_video_path)
            
            if os.path.exists(final_chunk_audio_path): os.remove(final_chunk_audio_path)

        if not dubbed_video_chunks:
            return JSONResponse(status_code=400, content={"error": "Không có phân đoạn video hợp lệ để nối."})

        list_file_path = os.path.abspath(os.path.join(TEMP_DIR, f"video_list_{original_video_name}.txt"))
        with open(list_file_path, "w", encoding="utf-8") as f:
            for chunk_v in dubbed_video_chunks:
                f.write(f"file '{os.path.abspath(chunk_v).replace('\\', '/')}'\n")

        final_output_name = f"DUBBED_ZALO_{original_video_name}"
        final_output_path = os.path.abspath(os.path.join(OUTPUT_DIR, final_output_name))

        cmd_concat = ['ffmpeg', '-y', '-f', 'concat', '-safe', '0', '-i', list_file_path, '-c', 'copy', final_output_path]
        result = subprocess.run(cmd_concat, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        
        if result.returncode != 0:
            raise Exception(f"FFmpeg concat lỗi: {result.stderr}")

        return FileResponse(path=final_output_path, media_type="video/mp4", filename=final_output_name)

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Lỗi Giai đoạn 3: {str(e)}"})