import os
import time
from translating import process_audio_gemini
CHUNKING_LENGHT_MS = 5 * 60  * 1000  

def process_chunk(chunk_index: int, chunk_path: str, start_time: float):
    try: 
        segments = process_audio_gemini(chunk_path)
        
        if segments:
            for segment in segments:
                segment["start"] += start_time
                segment["end"] += start_time
                
        return chunk_index, segments
    except Exception as e:
        print(f"[Warning] Lỗi xử lý chunk {chunk_index}: {str(e)}")
        return chunk_index, []