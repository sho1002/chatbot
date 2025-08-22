import asyncio
import base64
import io
import os
import re
import zipfile
import streamlit as st
import edge_tts
from pathlib import Path
from streamlit.components.v1 import html as st_html

# ---- 文分割（英文向け：略語等は必要に応じて調整） -------------------------
SENT_SPLIT = re.compile(r"""
    # 文末記号 . ? !
    (?P<sent>        # 取り出したい文全体
        .*?          # 最短一致
        [\.!?]       # 文末記号
        (?:["')\]]+)?# 直後のクオート/括弧があれば含める
    )
    (?=\s+|$)        # 次が空白 or 行末
""", re.VERBOSE | re.DOTALL)

def split_sentences(text: str):
    # 改行や余分な空白をある程度正規化
    text = re.sub(r'\s+', ' ', text.strip())
    sents = [m.group("sent").strip() for m in SENT_SPLIT.finditer(text)]
    # 終端に句点がない最後の一文も拾う
    if sents:
        tail = text[text.rfind(sents[-1])+len(sents[-1]):]
        if tail and tail.strip():
            sents.append(tail.strip())
    elif text:
        sents = [text]
    # 空要素除去
    return [s for s in sents if s]

# ---- ファイル名ユーティリティ ----------------------------------------------
def sanitize_filename(s: str, max_len: int = 80) -> str:
    """ファイル名に使えない文字を置換し、長すぎる場合は切り詰める"""
    # Windows/Unix で不正な文字を置換
    s = re.sub(r'[\\/*?:"<>|]', '_', s)
    # 制御文字を除去
    s = re.sub(r'[\x00-\x1f]', '', s)
    # 先頭・末尾のドット/空白は避ける（Windows対策）
    s = s.strip().strip('.')
    # 空になった場合の保険
    if not s:
        s = "sentence"
    # 長すぎる場合に省略（拡張子除く長さの制約を意識）
    if len(s) > max_len:
        s = s[:max_len].rstrip() + "…"
    return s

def ensure_unique_name(existing: set, base_name: str) -> str:
    """重複を避けるため同名があれば (2), (3)... を付与"""
    if base_name not in existing:
        existing.add(base_name)
        return base_name
    n = 2
    stem = base_name
    while True:
        candidate = f"{stem} ({n})"
        if candidate not in existing:
            existing.add(candidate)
            return candidate
        n += 1

# ---- 音声合成 ------------------------------------------------------------
async def synth_to_mp3(voice: str, rate: str, volume: str, text: str, out_path: str, timeout_sec: int = 30):
    """edge-tts で text を MP3 化して out_path へ保存（軽いタイムアウト付き）"""
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, volume=volume)
    await asyncio.wait_for(communicate.save(out_path), timeout=timeout_sec)

async def synth_all(sents, voice, rate, volume):
    """各文を '英文そのもの.mp3' で保存"""
    files = []
    used_names = set()
    for s in sents:
        safe_stem = sanitize_filename(s)
        unique_stem = ensure_unique_name(used_names, safe_stem)
        filename = f"{unique_stem}.mp3"
        await synth_to_mp3(voice, rate, volume, s, filename)
        files.append((filename, s))
    return files

# ---- ループ再生用プレイヤー ----------------------------------------------
def audio_player_html(audio_bytes: bytes, key: str, loop: bool = True):
    """HTML5 audio プレイヤーを埋め込み。loop=True で無限リピート。"""
    b64 = base64.b64encode(audio_bytes).decode("ascii")
    loop_attr = "loop" if loop else ""
    html = f"""
    <audio id="player-{key}" controls {loop_attr} style="width:100%;">
      <source src="data:audio/mp3;base64,{b64}" type="audio/mpeg">
      Your browser does not support the audio element.
    </audio>
    """
    st_html(html, height=54)

# ---- UI -------------------------------------------------------------------
st.set_page_config(page_title="Sentence-to-MP3", page_icon="🎧")
st.title("Sentence-to-MP3 (English) 🎧")

st.write("英文テキストを文ごとに分割し、各文を個別のMP3にします。**各MP3のファイル名は英文そのもの**になります。アプリ上で再生でき、最後にZIPで一括ダウンロードできます。")

with st.sidebar:
    st.header("Settings")
    voice = st.text_input("Voice (edge-tts)", value="en-US-JennyNeural")
    rate = st.text_input("Rate (e.g. +0%, -10%, +20%)", value="+0%")
    volume = st.text_input("Volume (e.g. +0%, +10%)", value="+0%")
    show_players = st.checkbox("生成後にプレイヤーを表示", value=True)
    loop_players = st.checkbox("🔁 ループ再生（∞）", value=False)

tab1, tab2 = st.tabs(["Paste text", "Upload .txt"])

text_input = ""
with tab1:
    text_input = st.text_area(
        "Paste your English text here",
        height=200,
        placeholder="Paste multiple English sentences..."
    )
with tab2:
    up = st.file_uploader("Upload a .txt file (UTF-8)", type=["txt"])
    if up:
        text_input = up.read().decode("utf-8", errors="ignore")

col_a, col_b = st.columns([1, 1])
with col_a:
    if st.button("Preview split"):
        sents = split_sentences(text_input)
        if not sents:
            st.warning("テキストが空か、文を検出できませんでした。")
        else:
            st.success(f"{len(sents)} 文に分割しました。下に一覧を表示します。")
            for i, s in enumerate(sents, 1):
                st.write(f"**{i:03}**: {s}")

with col_b:
    run = st.button("Generate MP3 & ZIP", type="primary")

if run:
    sents = split_sentences(text_input)
    if not sents:
        st.error("テキストが空か、文を検出できませんでした。")
    else:
        try:
            with st.spinner("Generating MP3 files..."):
                files = asyncio.run(synth_all(sents, voice, rate, volume))
        except asyncio.TimeoutError:
            st.error("音声合成がタイムアウトしました。ネットワーク環境をご確認ください。")
            st.stop()
        except Exception as e:
            st.error(f"音声合成に失敗しました：{e}")
            st.stop()

        # ZIPにまとめる（メモリ上）
        mem_zip = io.BytesIO()
        with zipfile.ZipFile(mem_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, _ in files:
                # arcname はファイル名そのまま（相対化）
                zf.write(fname, arcname=os.path.basename(fname))
        mem_zip.seek(0)

        st.success(f"生成完了：{len(files)} ファイル")
        st.download_button(
            label="Download ZIP",
            data=mem_zip,
            file_name="sentences_mp3.zip",
            mime="application/zip"
        )

        # --- ここから：アプリ内再生 ---
        if show_players:
            st.subheader("🔊 再生（文ごと）")
            with st.container():
                for i, (fname, s) in enumerate(files, 1):
                    try:
                        with open(fname, "rb") as f:
                            audio_bytes = f.read()
                        st.markdown(f"**{i:03}** &nbsp; {s}")
                        if loop_players:
                            audio_player_html(audio_bytes, key=f"{i:03}", loop=True)
                        else:
                            st.audio(audio_bytes, format="audio/mp3")
                    except Exception as e:
                        st.warning(f"{fname} の読み込みに失敗しました: {e}")

        # 片付け（ローカルに残さない）
        for fname, _ in files:
            try:
                Path(fname).unlink(missing_ok=True)
            except Exception:
                pass

st.caption("※ edge-tts はオンラインで音声を取得します。社内ネットワークのプロキシ等がある場合はご注意ください。ファイル名はOSの制約に合わせて一部の記号を置換・省略しています。")
