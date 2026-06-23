import os
import re
import logging
import requests
import translators as ts
from faster_whisper import WhisperModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    filename='whisper_pipeline.log',
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    encoding='utf-8'
)

MODEL_SIZE = "small"
print(f"[Whisper] Đang tải model '{MODEL_SIZE}' vào bộ nhớ RAM/CPU...")
whisper_model = WhisperModel(MODEL_SIZE, device="cpu", compute_type="int8")
print("[Whisper] Tải model thành công! Sẵn sàng xử lý.")

ZALO_TTS_URL = "https://api.zalo.ai/v1/tts/synthesize"
ZALO_API_KEY = os.getenv("ZALO_API_KEY", "") 

ZALO_SPEAKER_ID = os.getenv("ZALO_SPEAKER_ID", "6")  
ZALO_SPEED = os.getenv("ZALO_SPEED", "1")


def translate_to_vietnamese(text: str) -> str:
    if not text.strip():
        return ""
    try:
        translated = ts.translate_text(text, from_language='auto', to_language='vi', translator='google')
        return translated
    except Exception as e:
        logging.error(f"Lỗi khi dịch dòng [{text}]: {str(e)}")
        return text


def process_audio_gemini(audio_path: str, to: str = "vi"):
    if not os.path.exists(audio_path) or os.path.getsize(audio_path) == 0:
        logging.warning(f"File không tồn tại hoặc trống: {audio_path}")
        return []

    try:
        logging.info(f"=== BẮT ĐẦU NHẬN DIỆN & DỊCH FILE: {audio_path} ===")

        segments_generator, info = whisper_model.transcribe(
            audio_path,
            beam_size=5,
            word_timestamps=False
        )

        logging.info(f"Ngôn ngữ gốc phát hiện: '{info.language}' (Độ tự tin: {info.language_probability:.2f})")

        # 1. Chuyển Generator thành mảng để dễ xử lý gộp
        raw_segments = list(segments_generator)
        merged_segments = []

        if raw_segments:
            # Khởi tạo câu đầu tiên
            current_text = raw_segments[0].text.strip()
            current_start = raw_segments[0].start
            current_end = raw_segments[0].end

            # 2. VÒNG LẶP GỘP CÂU (MERGING ALGORITHM)
            for i in range(1, len(raw_segments)):
                seg = raw_segments[i]
                text = seg.text.strip()
                if not text: continue
                
                # Tính khoảng thời gian im lặng giữa 2 segment
                gap = seg.start - current_end
                
                # Kiểm tra xem câu hiện tại đã kết thúc bằng dấu ngắt câu chưa
                is_end_of_sentence = current_text.endswith(('.', '?', '!', ';'))
                
                # Tiêu chí GỘP: Khoảng nghỉ < 0.8s VÀ chưa hết câu VÀ độ dài hiện tại < 150 ký tự
                if gap < 0.8 and not is_end_of_sentence and len(current_text) < 150:
                    current_text += " " + text
                    current_end = seg.end  # Kéo dài thời gian kết thúc
                else:
                    # Tiêu chí NGẮT: Lưu câu đã gộp vào mảng
                    merged_segments.append({
                        "start": current_start,
                        "end": current_end,
                        "text": current_text
                    })
                    # Reset lại bộ đếm cho câu mới
                    current_start = seg.start
                    current_text = text
                    current_end = seg.end
            
            # đẩy câu cuối cùng vào mảng
            if current_text:
                merged_segments.append({
                    "start": current_start,
                    "end": current_end,
                    "text": current_text
                })

        # 3. MỚI BẮT ĐẦU DỊCH THUẬT SAU KHI ĐÃ GỘP CÂU
        final_segments = []
        count = 0
        for m_seg in merged_segments:
            text_original = m_seg["text"].strip()
            if not text_original: continue
            
            count += 1
            # Dịch theo NGỮ CẢNH CẢ CÂU DÀI -> Bản dịch sẽ chuẩn xác hơn nhiều
            text_vietnamese = translate_to_vietnamese(text_original)

            logging.info(f"Câu {count} [{m_seg['start']:.2f}s -> {m_seg['end']:.2f}s]")
            logging.info(f"   ├─ Gốc: {text_original}")
            logging.info(f"   └─ Dịch: {text_vietnamese}")

            final_segments.append({
                "start": float(m_seg["start"]),
                "end": float(m_seg["end"]),
                "text_en": text_original,
                "text_vi": text_vietnamese
            })

        logging.info(f"Kết thúc file. Đã tối ưu từ {len(raw_segments)} mảnh vụn xuống còn {count} câu hoàn chỉnh.")
        logging.info("================================================")
        print(f"[Pipeline] Tối ưu bóc băng: {len(raw_segments)} mảnh -> {count} câu của file {os.path.basename(audio_path)}.")
        
        return final_segments

    except Exception as e:
        logging.error(f"Lỗi hệ thống khi chạy Whisper + Translator với file {audio_path}: {str(e)}")
        return []

# =====================================================================
# ZALO TTS
# =====================================================================

MIN_TTS_CHARS = 3

def _sanitize_tts_text(text: str) -> str:
    """Làm sạch text trước khi đưa vào TTS"""
    text = text.strip()
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if text and text[-1] not in '.!?,;:…':
        text += '.'
    return text


def generate_vi_speech(text_vi: str, output_audio_path: str) -> bool:
    """
    Sinh giọng đọc tiếng Việt qua Zalo TTS API (đồng bộ, không cần async).
    Trả về True nếu thành công, False nếu thất bại.
    """
    if not ZALO_API_KEY:
        logging.error("[TTS] Chưa cấu hình ZALO_API_KEY trong file .env")
        return False

    clean_text = _sanitize_tts_text(text_vi)

    if len(clean_text) < MIN_TTS_CHARS:
        logging.warning(f"[TTS] Bỏ qua câu quá ngắn: '{clean_text}'")
        return False

    try:
        payload = {
            "input":      clean_text,
            "speaker_id": ZALO_SPEAKER_ID,
            "speed":      ZALO_SPEED,
            "encode_type": 1,   
        }
        headers = {
            "apikey":       ZALO_API_KEY,
            "Content-Type": "application/x-www-form-urlencoded",
        }

        resp = requests.post(ZALO_TTS_URL, data=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        result = resp.json()

        if result.get("error_code") != 0:
            logging.error(f"[TTS] Zalo API lỗi: {result.get('error_message')} | text: '{clean_text[:50]}'")
            return False

        audio_url = result["data"]["url"]

        # Bước 2: Tải file audio từ URL về disk
        audio_resp = requests.get(audio_url, timeout=30)
        audio_resp.raise_for_status()

        with open(output_audio_path, "wb") as f:
            f.write(audio_resp.content)

        if os.path.getsize(output_audio_path) == 0:
            logging.warning(f"[TTS] File audio rỗng cho câu: '{clean_text[:50]}'")
            return False

        logging.info(f"[TTS] OK ({os.path.getsize(output_audio_path)} bytes): '{clean_text[:50]}'")
        return audio_url

    except requests.exceptions.Timeout:
        logging.error(f"[TTS] Timeout khi gọi Zalo TTS cho câu: '{clean_text[:50]}'")
        return False
    except Exception as e:
        logging.error(f"[TTS] Lỗi không xác định: {str(e)} | text: '{clean_text[:50]}'")
        return False