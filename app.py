import os
import re
import base64
import shutil
import traceback
from datetime import datetime, date
import requests
import streamlit as st
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi

# =====================================================================
# [구역 1] 🎛️ 초보자용 설정 스위치 (수정이 필요할 때 여기만 고치세요!)
# =====================================================================
DEFAULT_PASSWORD = "1234"  # Streamlit Secrets를 안 썼을 때 사용할 기본 비밀번호
DEFAULT_SHORTS_MINUTES = 3 # 기본으로 제외할 짧은 영상(쇼츠) 기준 시간(분)
DEFAULT_CHANNELS = [       # 즐겨찾기에 기본으로 띄울 유튜브 채널 목록
    "직접 입력",
    "@3protv",
    "@ebsstory",
    "@ted"
]
# =====================================================================


st.set_page_config(page_title="유튜브 지식 수집 & 구글 드라이브 클라우드", layout="wide")

# 임시 폴더 준비
LOCAL_STORAGE_DIR = "./shared_cloud_storage"
os.makedirs(LOCAL_STORAGE_DIR, exist_ok=True)


# =====================================================================
# [구역 2] 🛡️ 안전 도우미 및 불량 방지 함수 (건드리지 마세요)
# =====================================================================
def get_secret(key, default_value=""):
    """Secrets 설정이 없어도 앱이 멈추지 않도록 안전하게 값을 가져옵니다."""
    try:
        if key in st.secrets:
            return st.secrets[key]
    except Exception:
        pass
    return default_value

def extract_gdrive_folder_id(folder_input):
    """구글 드라이브 폴더 링크 전체를 넣어도 자동으로 폴더 ID만 뽑아냅니다."""
    if not folder_input:
        return ""
    text = folder_input.strip()
    match = re.search(r'folders/([a-zA-Z0-9_-]+)', text)
    if match:
        return match.group(1)
    return text.split("?")[0]

def parse_duration_to_seconds(duration_str):
    """유튜브 시간 포맷을 초 단위로 변환합니다."""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str or "")
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s

def format_seconds(seconds):
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h > 0 else f"{m:02d}:{s:02d}"

def safe_get_transcript(video_id):
    """자막 프로그램 버전이 바뀌어도 신버전/구버전을 모두 시도해 고장을 막습니다."""
    # 1. 최신 버전(1.0+) 방식 시도
    try:
        api_obj = YouTubeTranscriptApi()
        if hasattr(api_obj, "fetch"):
            fetched = api_obj.fetch(video_id, languages=['ko', 'en'])
            if hasattr(fetched, "to_raw_data"):
                return fetched.to_raw_data()
            return [
                {
                    "text": getattr(x, "text", x.get("text", "") if isinstance(x, dict) else ""),
                    "start": getattr(x, "start", x.get("start", 0) if isinstance(x, dict) else 0)
                }
                for x in fetched
            ]
    except Exception:
        pass

    # 2. 구버전(0.6.x) 방식 시도
    if hasattr(YouTubeTranscriptApi, "get_transcript"):
        return YouTubeTranscriptApi.get_transcript(video_id, languages=['ko', 'en'])
    
    raise Exception("해당 영상에서 한국어/영어 자막을 찾을 수 없습니다.")

def show_ai_repair_box(block_name, error_obj):
    """불량 발생 시 코딩을 몰라도 AI에게 바로 수리를 맡길 수 있는 텍스트 박스를 띄웁니다."""
    st.error(f"⚠️ [{block_name}] 구역에서 예상치 못한 오류가 발생했습니다.")
    repair_text = (
        f"내 유튜브 자막 앱의 [{block_name}]에서 아래 오류가 났어. 내가 바로 복사해서 덮어쓸 수 있게 고쳐줘.\n"
        f"- 오류 메시지: {str(error_obj)}\n"
        f"- 상세 위치:\n{traceback.format_exc()}"
    )
    st.warning("👇 **코딩을 고칠 필요 없이 아래 상자의 글을 복사해서 제미나이에게 그대로 붙여넣으세요:**")
    st.code(repair_text, language="text")


# =====================================================================
# [구역 3] ☁️ 구글 드라이브 자동 업로드 및 목록 불러오기
# =====================================================================
def upload_to_gdrive_bridge(file_path, file_name, script_url, folder_id):
    """수집된 파일을 내 구글 드라이브 폴더로 즉시 전송합니다."""
    if not script_url or not folder_id:
        return False, "구글 드라이브 웹앱 URL 또는 폴더 링크가 입력되지 않았습니다."
    try:
        with open(file_path, "rb") as f:
            b64_content = base64.b64encode(f.read()).decode("utf-8")
        
        mime_type = "text/plain" if file_name.endswith(".txt") else "application/zip"
        payload = {
            "folderId": folder_id,
            "fileName": file_name,
            "mimeType": mime_type,
            "fileContentBase64": b64_content
        }
        res = requests.post(script_url.strip(), json=payload, timeout=60)
        data = res.json()
        if data.get("status") == "success":
            return True, data.get("fileUrl", "")
        return False, data.get("message", "구글 드라이브 응답 오류")
    except Exception as e:
        return False, f"구글 드라이브 전송 실패: {e}"

def list_gdrive_files(script_url, folder_id):
    """구글 드라이브 폴더 안에 저장된 파일 목록을 실시간으로 가져옵니다."""
    if not script_url or not folder_id:
        return False, []
    try:
        res = requests.get(
            script_url.strip(),
            params={"folderId": folder_id},
            timeout=20
        )
        data = res.json()
        if data.get("status") == "success":
            return True, data.get("files", [])
        return False, []
    except Exception:
        return False, []


# =====================================================================
# [구역 4] 🔒 로그인 화면 및 왼쪽 사이드바(설정/자가진단)
# =====================================================================
APP_PASSWORD = get_secret("APP_PASSWORD", DEFAULT_PASSWORD)

if "authenticated" not in st.session_state:
    st.session_state["authenticated"] = False

if not st.session_state["authenticated"]:
    st.title("🔒 나만의 유튜브 지식 클라우드 로그인")
    st.write("PC와 스마트폰, 그리고 지인들과 자료를 공유하는 전용 공간입니다.")
    pwd_input = st.text_input("비밀번호를 입력하세요", type="password")
    if st.button("로그인", type="primary"):
        if pwd_input == APP_PASSWORD:
            st.session_state["authenticated"] = True
            st.rerun()
        else:
            st.error("비밀번호가 맞지 않습니다. (초기 비밀번호는 1234 입니다)")
    st.stop()

# --- 사이드바 설정 ---
st.sidebar.header("🔑 1. 유튜브 & 채널 설정")
api_key = st.sidebar.text_input(
    "YouTube API 키",
    value=get_secret("YOUTUBE_API_KEY", ""),
    type="password"
)

presets = get_secret("FAVORITE_CHANNELS", DEFAULT_CHANNELS)
selected_preset = st.sidebar.selectbox("⭐ 자주 찾는 채널", presets)
if selected_preset == "직접 입력":
    channel_input = st.sidebar.text_input("채널 핸들 입력 (예: @3protv)")
else:
    channel_input = st.sidebar.text_input("채널 핸들 입력", value=selected_preset)

st.sidebar.divider()
st.sidebar.header("☁️ 2. 구글 드라이브 연결 설정")
gdrive_script_url = st.sidebar.text_input(
    "구글 드라이브 연결 URL (Apps Script)",
    value=get_secret("GDRIVE_SCRIPT_URL", ""),
    type="password",
    help="Secrets에 GDRIVE_SCRIPT_URL을 등록해두면 자동으로 채워집니다."
)
gdrive_folder_raw = st.sidebar.text_input(
    "구글 드라이브 공유 폴더 링크(또는 ID)",
    value=get_secret("GDRIVE_FOLDER_LINK", ""),
    help="구글 드라이브 폴더 주소를 그대로 붙여넣으세요."
)
gdrive_folder_id = extract_gdrive_folder_id(gdrive_folder_raw)

# --- 불량 자가 진단 버튼 ---
st.sidebar.divider()
if st.sidebar.button("🛠️ 어디가 불량인지 자가 진단하기", use_container_width=True):
    st.sidebar.subheader("🩺 진단 결과")
    # 1. 유튜브 API 진단
    if not api_key:
        st.sidebar.error("❌ 유튜브 API 키가 비어 있습니다.")
    else:
        try:
            yt_test = build('youtube', 'v3', developerKey=api_key)
            yt_test.videos().list(part="id", id="dQw4w9WgXcQ").execute()
            st.sidebar.success("✅ 유튜브 API 키: 정상 작동")
        except Exception as e:
            st.sidebar.error(f"❌ 유튜브 API 키 오류: {e}")

    # 2. 구글 드라이브 연결 진단
    if not gdrive_script_url or not gdrive_folder_id:
        st.sidebar.warning("⚠️ 구글 드라이브 URL 또는 폴더 링크가 아직 입력되지 않았습니다.")
    else:
        ok, _ = list_gdrive_files(gdrive_script_url, gdrive_folder_id)
        if ok:
            st.sidebar.success("✅ 구글 드라이브 연동: 정상 연결됨")
        else:
            st.sidebar.error("❌ 구글 드라이브 연결 실패: URL이나 폴더 공유 권한을 확인해주세요.")


# =====================================================================
# [구역 5] 📺 메인 화면 (자막 수집기 & 구글 드라이브 공유 보관함)
# =====================================================================
st.title("📚 유튜브 지식 수집 & 구글 드라이브 공유 콘솔")

tab_scraper, tab_cloud = st.tabs([
    "🔍 1. 유튜브 자막 수집하기",
    "☁️ 2. 구글 드라이브 공유 보관함"
])

if 'playlists' not in st.session_state:
    st.session_state['playlists'] = []
if 'channel_title' not in st.session_state:
    st.session_state['channel_title'] = ""

# --- 채널 불러오기 동작 ---
if st.sidebar.button("🔍 채널 재생목록 불러오기", type="primary", use_container_width=True):
    if not api_key or not channel_input:
        st.sidebar.warning("유튜브 API 키와 채널 핸들을 입력해주세요.")
    else:
        try:
            youtube = build('youtube', 'v3', developerKey=api_key)
            clean_ch = channel_input.strip()
            if clean_ch.startswith("@"):
                res = youtube.channels().list(
                    part="id,snippet,contentDetails", forHandle=clean_ch.lstrip('@')
                ).execute()
            else:
                res = youtube.channels().list(
                    part="id,snippet,contentDetails", id=clean_ch
                ).execute()

            if not res.get('items'):
                st.sidebar.error("채널을 찾을 수 없습니다. @채널이름을 확인해주세요.")
            else:
                ch_info = res['items'][0]
                ch_id = ch_info['id']
                ch_title = ch_info['snippet']['title']
                uploads_id = ch_info['contentDetails']['relatedPlaylists']['uploads']

                st.session_state['channel_title'] = ch_title
                playlists = [{"id": uploads_id, "title": f"🔥 [채널 전체 업로드 영상] ({ch_title})"}]

                req = youtube.playlists().list(part="snippet", channelId=ch_id, maxResults=50)
                while req:
                    pl_res = req.execute()
                    for item in pl_res.get('items', []):
                        playlists.append({"id": item['id'], "title": item['snippet']['title']})
                    req = youtube.playlists().list_next(req, pl_res)

                st.session_state['playlists'] = playlists
                st.sidebar.success(f"'{ch_title}' 불러오기 성공!")
        except Exception as e:
            show_ai_repair_box("채널 불러오기", e)

# --- 탭 1: 자막 수집기 ---
with tab_scraper:
    if not st.session_state['playlists']:
        st.info("👈 왼쪽 메뉴에서 **[🔍 채널 재생목록 불러오기]** 버튼을 먼저 눌러주세요.")
    else:
        st.subheader(f"📺 [{st.session_state['channel_title']}] 원하는 재생목록 체크 (중복 가능)")
        selected_playlists = []
        cols = st.columns(2)
        for idx, pl in enumerate(st.session_state['playlists']):
            with cols[idx % 2]:
                if st.checkbox(pl['title'], key=f"pl_{pl['id']}"):
                    selected_playlists.append(pl)

        st.divider()
        st.subheader("⚙️ 기간 정렬 및 필터 설정")
        col1, col2, col3 = st.columns(3)

        with col1:
            use_date_filter = st.checkbox("📅 특정 기간만 골라내기", value=False)
            start_date = st.date_input("시작일", value=date(2024, 1, 1), disabled=not use_date_filter)
            end_date = st.date_input("종료일", value=date.today(), disabled=not use_date_filter)
            sort_order = st.radio("정렬 순서", ["최신순 (최근 영상부터)", "오래된순 (과거 영상부터)"])

        with col2:
            min_minutes = st.number_input("⏱️ 쇼츠 제외 (분 이하 제외)", min_value=0, max_value=60, value=DEFAULT_SHORTS_MINUTES)
            keyword_filter = st.text_input("🔎 제목 키워드 포함 (선택)", placeholder="예: 독서, 육아, 반도체")

        with col3:
            merge_option = st.radio(
                "📄 파일 저장 방식",
                ["전체 1개 파일로 합치기 (.txt) - 추천", "재생목록별로 합치기 (.zip)", "영상별로 따로 만들기 (.zip)"]
            )
            include_timestamp = st.checkbox("🕒 시간 표시([03:15]) 포함", value=True)
            auto_gdrive = st.checkbox("☁️ 구글 드라이브에 자동 저장하기", value=True)

        st.divider()

        if st.button("🚀 자막 수집 및 구글 드라이브 전송 시작", type="primary", use_container_width=True):
            if not selected_playlists:
                st.warning("최소 1개 이상의 재생목록을 체크해주세요.")
            else:
                try:
                    youtube = build('youtube', 'v3', developerKey=api_key)
                    status_box = st.empty()
                    progress_bar = st.progress(0)

                    # 1단계: 영상 목록 가져오기
                    status_box.info("1/3단계: 선택한 목록에서 영상들을 모으고 있습니다...")
                    raw_videos_by_pl = {}
                    all_video_ids = set()

                    for pl in selected_playlists:
                        raw_videos_by_pl[pl['title']] = []
                        req = youtube.playlistItems().list(part="contentDetails", playlistId=pl['id'], maxResults=50)
                        while req:
                            res = req.execute()
                            for item in res.get('items', []):
                                vid = item['contentDetails']['videoId']
                                raw_videos_by_pl[pl['title']].append(vid)
                                all_video_ids.add(vid)
                            req = youtube.playlistItems().list_next(req, res)

                    # 2단계: 날짜 및 쇼츠 필터링
                    status_box.info(f"2/3단계: 총 {len(all_video_ids)}개 영상의 날짜와 길이를 확인 중입니다...")
                    video_details = {}
                    vid_list = list(all_video_ids)

                    for i in range(0, len(vid_list), 50):
                        batch = vid_list[i:i+50]
                        v_res = youtube.videos().list(part="snippet,contentDetails", id=",".join(batch)).execute()
                        for item in v_res.get('items', []):
                            vid = item['id']
                            title = item['snippet']['title']
                            pub_str = item['snippet']['publishedAt'][:10]
                            pub_date = datetime.strptime(pub_str, "%Y-%m-%d").date()
                            dur_sec = parse_duration_to_seconds(item['contentDetails']['duration'])

                            if dur_sec < (min_minutes * 60):
                                continue
                            if use_date_filter and not (start_date <= pub_date <= end_date):
                                continue
                            if keyword_filter.strip() and (keyword_filter.strip().lower() not in title.lower()):
                                continue

                            video_details[vid] = {
                                "id": vid,
                                "title": title,
                                "pub_date": pub_str,
                                "dur_str": format_seconds(dur_sec),
                                "url": f"https://www.youtube.com/watch?v={vid}"
                            }

                    if not video_details:
                        status_box.warning("조건에 맞는 영상이 없습니다. 날짜나 시간 조건을 넓혀보세요.")
                        st.stop()

                    # 날짜 정렬
                    reverse_flag = True if "최신순" in sort_order else False
                    sorted_vids = sorted(
                        video_details.keys(),
                        key=lambda x: video_details[x]['pub_date'],
                        reverse=reverse_flag
                    )

                    # 3단계: 자막 추출
                    total_cnt = len(sorted_vids)
                    transcripts_data = {}
                    failed_videos = []

                    for idx, vid in enumerate(sorted_vids):
                        v_info = video_details[vid]
                        status_box.text(f"[{idx+1}/{total_cnt}] 자막 추출 중: ({v_info['pub_date']}) {v_info['title']}")
                        progress_bar.progress((idx + 1) / total_cnt)

                        try:
                            t_list = safe_get_transcript(vid)
                            lines = []
                            for t in t_list:
                                if include_timestamp:
                                    lines.append(f"[{format_seconds(t['start'])}] {t['text']}")
                                else:
                                    lines.append(t['text'])
                            body = ("\n" if include_timestamp else " ").join(lines)

                            header = (
                                f"==================================================\n"
                                f"■ 채널명: {st.session_state['channel_title']}\n"
                                f"■ 영상 제목: {v_info['title']}\n"
                                f"■ 업로드일: {v_info['pub_date']} | 길이: {v_info['dur_str']}\n"
                                f"■ 원본 링크: {v_info['url']}\n"
                                f"==================================================\n\n"
                            )
                            transcripts_data[vid] = header + body + "\n\n\n"
                        except Exception:
                            failed_videos.append(v_info)

                    # 파일 만들기
                    now_str = datetime.now().strftime("%Y%m%d_%H%M")
                    safe_ch = "".join([c for c in st.session_state['channel_title'] if c.isalnum() or c in (' ', '_')]).rstrip()
                    work_dir = "./temp_work"
                    if os.path.exists(work_dir):
                        shutil.rmtree(work_dir)
                    os.makedirs(work_dir, exist_ok=True)

                    if merge_option.startswith("전체 1개"):
                        final_name = f"{safe_ch}_통합본_{now_str}.txt"
                        final_path = os.path.join(work_dir, final_name)
                        with open(final_path, "w", encoding="utf-8") as f:
                            for vid in sorted_vids:
                                if vid in transcripts_data:
                                    f.write(transcripts_data[vid])
                    elif merge_option.startswith("재생목록별"):
                        for pl_title, vids in raw_videos_by_pl.items():
                            safe_pl = "".join([c for c in pl_title if c.isalnum() or c in (' ', '_')]).rstrip()
                            ordered = [v for v in sorted_vids if v in vids and v in transcripts_data]
                            if ordered:
                                with open(os.path.join(work_dir, f"{safe_ch}_{safe_pl}.txt"), "w", encoding="utf-8") as f:
                                    for v in ordered:
                                        f.write(transcripts_data[v])
                        final_name = f"{safe_ch}_재생목록별_{now_str}.zip"
                        shutil.make_archive(os.path.join(work_dir, "archive"), 'zip', work_dir)
                        final_path = os.path.join(work_dir, "archive.zip")
                    else:
                        for vid in sorted_vids:
                            if vid in transcripts_data:
                                v_info = video_details[vid]
                                safe_v = "".join([c for c in v_info['title'] if c.isalnum() or c in (' ', '_')]).rstrip()
                                with open(os.path.join(work_dir, f"{v_info['pub_date']}_{safe_v}.txt"), "w", encoding="utf-8") as f:
                                    f.write(transcripts_data[vid])
                        final_name = f"{safe_ch}_개별파일_{now_str}.zip"
                        shutil.make_archive(os.path.join(work_dir, "archive"), 'zip', work_dir)
                        final_path = os.path.join(work_dir, "archive.zip")

                    # 로컬 백업 및 구글 드라이브 자동 업로드
                    shutil.copy(final_path, os.path.join(LOCAL_STORAGE_DIR, final_name))
                    status_box.success(f"🎉 자막 추출 완료! (성공: {len(transcripts_data)}개 / 자막 없음: {len(failed_videos)}개)")

                    if auto_gdrive and gdrive_script_url and gdrive_folder_id:
                        with st.spinner("☁️ 내 구글 드라이브 폴더로 파일을 전송하고 있습니다..."):
                            ok, g_msg = upload_to_gdrive_bridge(final_path, final_name, gdrive_script_url, gdrive_folder_id)
                            if ok:
                                st.success(f"✅ 구글 드라이브 자동 저장 완료! 👉 [구글 드라이브에서 파일 바로 열기]({g_msg})")
                            else:
                                st.warning(f"⚠️ 구글 드라이브 저장 실패 (하단 다운로드 버튼을 이용하세요): {g_msg}")

                    with open(final_path, "rb") as fp:
                        st.download_button(
                            label=f"📥 내 기기로 즉시 다운로드 ({final_name})",
                            data=fp,
                            file_name=final_name,
                            mime="text/plain" if final_name.endswith(".txt") else "application/zip",
                            type="primary"
                        )

                    if failed_videos:
                        with st.expander(f"⚠️ 자막이 없어서 제외된 영상 확인하기 ({len(failed_videos)}개)"):
                            for fv in failed_videos:
                                st.markdown(f"- [{fv['pub_date']}] [{fv['title']}]({fv['url']})")

                except Exception as e:
                    show_ai_repair_box("자막 수집 및 저장", e)

# --- 탭 2: 구글 드라이브 공유 보관함 ---
with tab_cloud:
    st.subheader("☁️ 구글 드라이브 실시간 공유 보관함")
    if gdrive_folder_id:
        folder_url = f"https://drive.google.com/drive/folders/{gdrive_folder_id}"
        st.markdown(f"👉 **[📂 내 구글 드라이브 공유 폴더 전체 열기 (클릭)]({folder_url})**")
        st.write("이 폴더 링크를 다른 사람에게 공유하거나, **구글 NotebookLM**에서 [구글 드라이브 소스 추가]로 바로 불러올 수 있습니다.")

    if st.button("🔄 구글 드라이브 폴더 목록 새로고침"):
        st.rerun()

    if gdrive_script_url and gdrive_folder_id:
        ok, g_files = list_gdrive_files(gdrive_script_url, gdrive_folder_id)
        if ok and g_files:
            for gf in g_files:
                c1, c2, c3 = st.columns([6, 2, 2])
                c1.markdown(f"**📄 {gf['name']}** (`{gf['sizeKb']} KB`)")
                c2.caption(f"저장일: {gf['date']}")
                c3.markdown(f"[🔗 드라이브에서 열기/다운로드]({gf['url']})")
        elif ok and not g_files:
            st.info("구글 드라이브 폴더가 아직 비어 있습니다. 첫 자막을 수집해 보세요!")
        else:
            st.warning("구글 드라이브 목록을 불러오지 못했습니다. 왼쪽 메뉴의 [자가 진단하기]를 눌러보세요.")
    else:
        st.info("구글 드라이브 연결 설정 전에는 임시 서버 보관함의 파일이 아래에 표시됩니다.")
        for fname in sorted(os.listdir(LOCAL_STORAGE_DIR), reverse=True):
            fpath = os.path.join(LOCAL_STORAGE_DIR, fname)
            with open(fpath, "rb") as fp:
                st.download_button(label=f"⬇️ {fname} 다운로드", data=fp, file_name=fname, key=f"local_{fname}")