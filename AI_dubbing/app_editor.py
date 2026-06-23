import os
import time
import glob
import streamlit as st
import pandas as pd
import requests
import streamlit.components.v1 as components
import json

# --- CẤU HÌNH HỆ THỐNG ---
st.set_page_config(page_title="AI Dubbing Studio Pro", layout="wide", initial_sidebar_state="expanded")
TEMP_DIR = "temp_files"
OUTPUT_DIR = "output_videos"
BASE_URL = "http://127.0.0.1:8000"

st.title("🎬 AI Dubbing Studio Pro - Quản lý trọn gói")

# --- QUẢN LÝ STATE ---
if "video_name" not in st.session_state: st.session_state.video_name = None
if "chunks_info" not in st.session_state: st.session_state.chunks_info = []
if "manifest_data" not in st.session_state: st.session_state.manifest_data = []
if "selected_track_id" not in st.session_state: st.session_state.selected_track_id = 0

def load_manifest(video_name):
    manifest_filepath = os.path.join(TEMP_DIR, f"manifest_{video_name}.log")
    if not os.path.exists(manifest_filepath): return []
    
    data = []
    with open(manifest_filepath, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f.readlines()[1:]):
            parts = line.split(" | ")
            if len(parts) >= 5:
                data.append({
                    "id": idx,
                    "chunk_index": int(parts[0]),
                    "text_en": parts[1].strip(),
                    "text_vi": parts[2].strip(),
                    "zalo_url": parts[3].strip(),
                    "start": float(parts[4].strip().split("-")[0]),
                    "end": float(parts[4].strip().split("-")[1])
                })
    return data

def change_track(new_track_id):
    st.session_state.selected_track_id = new_track_id

# =====================================================================
# SIDEBAR: QUẢN LÝ QUY TRÌNH & LỊCH SỬ DỰ ÁN
# =====================================================================
with st.sidebar:
    st.header("⚙️ Bảng điều khiển")
    
    st.markdown("### 🕒 Lịch sử Dự án")
    manifest_files = glob.glob(os.path.join(TEMP_DIR, "manifest_*.log"))
    if manifest_files:
        past_projects = [os.path.basename(f).replace("manifest_", "").replace(".log", "") for f in manifest_files]
        selected_past_project = st.selectbox("Mở lại dự án cũ:", ["-- Chọn dự án --"] + past_projects)
        
        if st.button("📂 Tải dữ liệu dự án này", use_container_width=True):
            if selected_past_project != "-- Chọn dự án --":
                st.session_state.video_name = selected_past_project
                st.session_state.manifest_data = load_manifest(selected_past_project)
                st.session_state.selected_track_id = 0
                
                meta_path = os.path.join(TEMP_DIR, f"meta_{selected_past_project}.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        st.session_state.chunks_info = json.load(f)
                else:
                    unique_chunks = sorted(list(set([item["chunk_index"] for item in st.session_state.manifest_data])))
                    reconstructed_chunks = []
                    for c_idx in unique_chunks:
                        reconstructed_chunks.append({
                            "chunk_index": c_idx,
                            "audio_path": f"{TEMP_DIR}/audio_chunk_{c_idx}.mp3",
                            "video_path": f"{TEMP_DIR}/video_chunk_{c_idx}.mp4",
                            "duration_ms": 300000, 
                            "global_start_sec": c_idx * 300.0
                        })
                    st.session_state.chunks_info = reconstructed_chunks
                    
                st.success(f"Đã khôi phục phiên làm việc!")
                st.rerun()
    else:
        st.info("Chưa có dự án nào trong lịch sử.")

    st.markdown("---")
    
    st.markdown("### 1. Upload Video Mới")
    chunk_mins = st.number_input("Thời lượng chia nhỏ (phút tối đa 10):", min_value=1, max_value=10, value=5)
    uploaded_file = st.file_uploader("Chọn video (.mp4)", type=["mp4", "mkv", "mov"])
    if st.button("Tải lên & Chia nhỏ", type="primary", use_container_width=True) and uploaded_file:
        with st.spinner(f"Đang cắt nhỏ video thành các đoạn {chunk_mins} phút..."):
            files = {"file": (uploaded_file.name, uploaded_file.getvalue(), uploaded_file.type)}
            data = {"chunk_length_sec": int(chunk_mins * 60)} 
            res = requests.post(f"{BASE_URL}/api/v1/upload-chunking", files=files, data=data)
            if res.status_code == 200:
                st.session_state.video_name = res.json()["original_video"]
                st.session_state.chunks_info = res.json()["chunks"]
                st.session_state.manifest_data = [] 
                
                meta_path = os.path.join(TEMP_DIR, f"meta_{st.session_state.video_name}.json")
                with open(meta_path, "w", encoding="utf-8") as f:
                    json.dump(st.session_state.chunks_info, f, ensure_ascii=False, indent=4)
                    
                st.success("Tải lên thành công!")
            else:
                st.error("Lỗi tải lên!")

    st.markdown("---")
    st.markdown("### 3. Xuất Video (Giai đoạn 3)")
    if st.button("Ghép nối & Xuất Video Cuối", type="primary", use_container_width=True):
        if st.session_state.chunks_info and st.session_state.video_name:
            with st.spinner("Đang ghép âm thanh và render video thành phẩm..."):
                res = requests.post(f"{BASE_URL}/api/v1/merge-dubbing?original_video_name={st.session_state.video_name}", json=st.session_state.chunks_info)
                if res.status_code == 200:
                    st.success("🎉 Render thành công!")
                    output_path = os.path.join(OUTPUT_DIR, f"DUBBED_{st.session_state.video_name}")
                    with open(output_path, "wb") as f:
                        f.write(res.content)
                    st.video(output_path)
                else:
                    st.error("Lỗi Render Video!")


# =====================================================================
# KHU VỰC MAIN: XỬ LÝ ĐỘC LẬP TỪNG CHUNK & TRÌNH CHỈNH SỬA TRACK
# =====================================================================
if not st.session_state.chunks_info:
    st.info("👈 Hãy bắt đầu bằng cách Upload Video mới hoặc Mở lại dự án cũ ở thanh bên trái.")
else:
    chunk_list = [c["chunk_index"] for c in st.session_state.chunks_info]
    selected_chunk = st.selectbox("📂 Chọn Phân đoạn Video để làm việc:", chunk_list, format_func=lambda x: f"Phân đoạn {x}")
    
    chunk_manifest = [m for m in st.session_state.manifest_data if m["chunk_index"] == selected_chunk]
    chunk_video_path = os.path.join(TEMP_DIR, f"video_chunk_{selected_chunk}.mp4")

    # 🔴 Lấy thông tin của Phân đoạn hiện tại để tính toán global_start_sec
    target_chunk_data = [c for c in st.session_state.chunks_info if c["chunk_index"] == selected_chunk]
    chunk_global_start = target_chunk_data[0]["global_start_sec"] if target_chunk_data else 0.0

    if not chunk_manifest:
        st.warning("⚠️ Phân đoạn này chưa có dữ liệu dịch thuật (Chưa bóc băng).")
        col_v1, col_v2, col_v3 = st.columns([1, 2, 1])
        with col_v2:
            if os.path.exists(chunk_video_path): st.video(chunk_video_path)
            
            if st.button(f"⚡ Bóc băng & Dịch riêng Phân đoạn {selected_chunk}", type="primary", use_container_width=True):
                with st.spinner(f"AI đang bóc băng phân đoạn {selected_chunk}..."):
                    res = requests.post(f"{BASE_URL}/api/v1/whisper-translate?original_video_name={st.session_state.video_name}", json=target_chunk_data)
                    if res.status_code == 200:
                        st.session_state.manifest_data = load_manifest(st.session_state.video_name)
                        new_manifest = [m for m in st.session_state.manifest_data if m["chunk_index"] == selected_chunk]
                        if new_manifest: st.session_state.selected_track_id = new_manifest[0]["id"]
                        st.success("Dịch xong phân đoạn!")
                        time.sleep(0.5)
                        st.rerun()
    else:
        chunk_df = pd.DataFrame(chunk_manifest)
        track_ids = chunk_df["id"].tolist()
        
        if st.session_state.selected_track_id not in track_ids and track_ids:
            st.session_state.selected_track_id = track_ids[0]

        current_row = chunk_df[chunk_df["id"] == st.session_state.selected_track_id].iloc[0]

        # --- VIDEO PREVIEW TĨNH ---
        play_original_audio = st.toggle("🔊 Bật âm thanh gốc của Video", value=False)
        col_v1, col_v2, col_v3 = st.columns([1, 2, 1])
        with col_v2:
            if os.path.exists(chunk_video_path):
                st.video(chunk_video_path)

        # --- ĐIỀU HƯỚNG TRACK ---
        st.write("🕹️ **Thanh điều hướng Timeline (Grid Menu):**")
        BUTTONS_PER_ROW = 15 
        for i in range(0, len(track_ids), BUTTONS_PER_ROW):
            cols_click = st.columns(BUTTONS_PER_ROW)
            for j, t_id in enumerate(track_ids[i : i + BUTTONS_PER_ROW]):
                with cols_click[j]:
                    btn_type = "primary" if t_id == st.session_state.selected_track_id else "secondary"
                    # 🔴 Bỏ ký tự # để giao diện không bị tách số xuống hàng
                    st.button(f"{t_id}", key=f"btn_nav_{t_id}", type=btn_type, use_container_width=True, on_click=change_track, args=(t_id,))

        st.markdown("---")

        # --- CHỈNH SỬA CHI TIẾT ---
        st.markdown(f"### 🛠️ Bộ hiệu chỉnh: **Track {st.session_state.selected_track_id}**")
        c1, c2, c3 = st.columns([2, 2, 1])
        
        with c1:
            st.info(f"**EN:** {current_row['text_en']}")
            new_text_vi = st.text_area("Bản dịch Tiếng Việt:", value=current_row['text_vi'], height=80)
        
        with c2:
            st.markdown("**Căn chỉnh mốc thời gian (Nhập số hoặc Kéo):**")
            col_t1, col_t2 = st.columns(2)
            with col_t1: 
                input_start = st.number_input("Bắt đầu (s)", min_value=0.0, max_value=3600.0, value=float(current_row['start']), step=0.05, format="%.2f")
            with col_t2: 
                input_end = st.number_input("Kết thúc (s)", min_value=0.0, max_value=3600.0, value=float(current_row['end']), step=0.05, format="%.2f")
                
            time_slider = st.slider("Thanh trượt", min_value=0.0, max_value=max(300.0, input_end + 50), value=(input_start, input_end), step=0.05, label_visibility="collapsed")
            new_start, new_end = time_slider
            
            if input_start != current_row['start'] or input_end != current_row['end']:
                new_start, new_end = input_start, input_end

        with c3:
            st.markdown("**Audio Zalo AI:**")
            
            if current_row['zalo_url'] != "N/A" and current_row['zalo_url'].startswith("http"):
                unique_ts = int(time.time() * 1000)
                raw_url = current_row['zalo_url']
                safe_url = f"{raw_url}&t={unique_ts}" if "?" in raw_url else f"{raw_url}?t={unique_ts}"
                
                audio_html = f"""
                    <audio id="aud_{unique_ts}" src="{safe_url}" controls autoplay style="width: 100%; border-radius: 8px; outline: none;"></audio>
                    <script>
                        var audio = document.getElementById("aud_{unique_ts}"); 
                        audio.load(); 
                        audio.play().catch(e => console.log("Audio chặn:", e));
                    </script>
                """
                components.html(audio_html, height=60)
            else:
                st.caption("Chưa có âm thanh")

        local_start = max(0.0, new_start - chunk_global_start)
        js_mute = "false" if play_original_audio else "true"
        
        video_sync_html = f"""
            <script>
                function syncVideo() {{
                    var parentDoc = window.parent.document;
                    var videos = parentDoc.getElementsByTagName("video");
                    if (videos.length > 0) {{ 
                        var vid = videos[0];
                        vid.muted = {js_mute};
                        
                        var doSync = function() {{
                            vid.currentTime = {local_start}; // 🔴 Truyền Local Time đã trừ ở trên vào đây
                            var p = vid.play();
                            if(p !== undefined) p.catch(e => console.log("Video autoplay chặn:", e));
                        }};

                        if (vid.readyState >= 1) {{
                            doSync();
                        }} else {{
                            vid.addEventListener('loadedmetadata', doSync, {{once: true}});
                        }}
                    }} else {{
                        setTimeout(syncVideo, 100);
                    }}
                }}
                syncVideo();
            </script>
        """
        components.html(video_sync_html, height=0)

        # --- LƯU TRỮ VÀ XỬ LÝ ---
        for idx, orig in enumerate(st.session_state.manifest_data):
            if orig['id'] == st.session_state.selected_track_id:
                st.session_state.manifest_data[idx].update({"text_vi": new_text_vi, "start": new_start, "end": new_end})

        st.markdown("### 🔍 Trạng thái đồng bộ trục âm thanh toàn phân đoạn")
        has_overlap = False
        fresh_df = pd.DataFrame(st.session_state.manifest_data)
        fresh_chunk_df = fresh_df[fresh_df["chunk_index"] == selected_chunk].copy()
        sorted_updated = fresh_chunk_df.sort_values(by="start").to_dict(orient="records")
        
        for i in range(len(sorted_updated) - 1):
            current_item = sorted_updated[i]
            next_item = sorted_updated[i+1]
            if current_item['end'] > next_item['start']:
                has_overlap = True
                st.error(f"🚨 **Đè tiếng:** Track {current_item['id']} ({current_item['end']}s) đè vào Track {next_item['id']} ({next_item['start']}s).")
                
        if not has_overlap:
            st.success("✅ Trục âm thanh đồng bộ hoàn hảo! Không có câu nào nói đè lên nhau.")

        @st.dialog("⚠️ Xác nhận lưu & Tạo âm thanh mới")
        def confirm_save_dialog(track_id, text_vi, start_t, end_t):
            st.write(f"Bạn có chắc chắn muốn cập nhật Track **{track_id}** và gọi Zalo AI để sinh lại giọng đọc không?")
            st.info(f"Nội dung mới: {text_vi}")
            if st.button("Đồng ý & Xử lý", type="primary"):
                for idx, orig in enumerate(st.session_state.manifest_data):
                    if orig['id'] == track_id:
                        st.session_state.manifest_data[idx].update({"text_vi": text_vi, "start": start_t, "end": end_t})
                
                payload = []
                for orig in st.session_state.manifest_data:
                    payload.append({
                        "chunk_index": int(current_row["chunk_index"]),
                        "text_en": orig["text_en"],
                        "text_vi": orig["text_vi"],
                        "start": float(orig["start"]),
                        "end": float(orig["end"])
                    })
                
                res = requests.post(f"{BASE_URL}/api/v1/generate-zalo-manifest?original_video_name={st.session_state.video_name}", json=payload)
                if res.status_code == 200:
                    st.session_state.manifest_data = load_manifest(st.session_state.video_name)
                    st.rerun()

        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("💾 Lưu bản dịch & Sinh âm thanh mới", type="primary"):
            confirm_save_dialog(st.session_state.selected_track_id, new_text_vi, new_start, new_end)