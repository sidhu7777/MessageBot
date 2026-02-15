import os
import sys
import time
import tempfile
import wave
import numpy as np
import pyaudio
import pyttsx3
import asyncio
import subprocess
from faster_whisper import WhisperModel

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.fsm.appointment_fsm import AppointmentFSM
from src.llm.client import LLMClient

# =====================================================
# CONFIG
# =====================================================
ASR_MODEL = "small"  # multilingual
LLM_MODEL = "qwen3:0.6b-instruct"

RATE = 16000
CHUNK = 1024
SILENCE_THRESHOLD = 150
SILENCE_SECONDS = 2.0


# =====================================================
# AUDIO UTILS
# =====================================================
def _ffplay_path():
    local = os.path.join(ROOT, "ffmpeg-8.0.1-essentials_build", "bin", "ffplay.exe")
    if os.path.exists(local):
        return local
    return "ffplay"


async def _edge_tts_speak(text, voice="hi-IN-SwaraNeural"):
    try:
        import edge_tts
    except Exception:
        return False

    try:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
        tmp.close()
        communicate = edge_tts.Communicate(text, voice=voice)
        await communicate.save(tmp.name)
        subprocess.run([_ffplay_path(), "-autoexit", "-nodisp", tmp.name], check=False)
        os.remove(tmp.name)
        return True
    except Exception:
        return False


def speak(text, voice_lang=None):
    print(f"\nAssistant: {text}")
    try:
        if voice_lang in ("hi", "hinglish"):
            ok = asyncio.run(_edge_tts_speak(text, voice="hi-IN-SwaraNeural"))
            if ok:
                time.sleep(0.2)
                return

        engine = pyttsx3.init()
        engine.setProperty("rate", 165)
        engine.setProperty("volume", 1.0)
        if voice_lang:
            for v in engine.getProperty("voices"):
                if voice_lang.lower() in v.id.lower() or voice_lang.lower() in v.name.lower():
                    engine.setProperty("voice", v.id)
                    break
        engine.say(text)
        engine.runAndWait()
        engine.stop()
    except Exception:
        pass
    time.sleep(0.2)


def listen_beep():
    try:
        duration = 0.12
        freq = 1000
        t = np.linspace(0, duration, int(RATE * duration), False)
        tone = (0.4 * np.sin(freq * 2 * np.pi * t)).astype(np.float32)
        p = pyaudio.PyAudio()
        stream = p.open(format=pyaudio.paFloat32, channels=1, rate=RATE, output=True)
        stream.write(tone.tobytes())
        stream.stop_stream()
        stream.close()
        p.terminate()
    except Exception:
        pass


def record_audio():
    p = pyaudio.PyAudio()
    stream = p.open(
        format=pyaudio.paInt16,
        channels=1,
        rate=RATE,
        input=True,
        frames_per_buffer=CHUNK
    )
    frames = []
    silent = 0
    started = False

    for _ in range(int(RATE / CHUNK * 15)):
        data = stream.read(CHUNK, exception_on_overflow=False)
        vol = np.abs(np.frombuffer(data, np.int16)).mean()

        if vol > SILENCE_THRESHOLD:
            if not started:
                started = True
                frames = []
            frames.append(data)
            silent = 0
        elif started:
            frames.append(data)
            silent += 1

        if started and silent > int(SILENCE_SECONDS * RATE / CHUNK):
            break

    stream.stop_stream()
    stream.close()
    p.terminate()

    if not started or len(frames) < int(0.6 * RATE / CHUNK):
        return None

    f = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    wf = wave.open(f.name, "wb")
    wf.setnchannels(1)
    wf.setsampwidth(2)
    wf.setframerate(RATE)
    wf.writeframes(b"".join(frames))
    wf.close()
    return f.name


# =====================================================
# WHISPER
# =====================================================
print("Loading Whisper...")
whisper = WhisperModel(ASR_MODEL, device="cpu", compute_type="int8")
print("Whisper loaded ✓")


def transcribe(path, language=None):
    segments, info = whisper.transcribe(
        path,
        language=language,
        vad_filter=True,
        beam_size=5,
        temperature=0,
        condition_on_previous_text=False
    )
    os.remove(path)

    text = " ".join(s.text.strip() for s in segments).strip()
    if not text:
        return "", None
    return text, getattr(info, "language", None)


# =====================================================
# MAIN
# =====================================================
def main():
    fsm = AppointmentFSM(llm_client=LLMClient(model=LLM_MODEL), mixed_response_language="hi")
    print("Select language mode:")
    print("1) Auto")
    print("2) English")
    print("3) Hindi")
    choice = input("Enter 1/2/3 (default 1): ").strip()
    if choice == "2":
        forced_lang = "en"
    elif choice == "3":
        forced_lang = "hi"
    else:
        forced_lang = None

    speak("Hello. I can help you book a medical appointment. How can I help you?")

    while True:
        try:
            print("\nListening...")
            listen_beep()
            audio = record_audio()
            if not audio:
                continue

            text, lang = transcribe(audio, language=forced_lang)
            if not text:
                continue

            print(f"You: {text} (lang={lang})")
            response = fsm.handle(text)
            if forced_lang == "hi":
                voice_lang = "hi"
            elif forced_lang == "en":
                voice_lang = "en"
            else:
                voice_lang = "hi" if lang in ("hi", "mixed") else "en"
            speak(response, voice_lang=voice_lang)

        except KeyboardInterrupt:
            print("\nShutting down...")
            break
        except Exception as e:
            print(f"Error: {e}")
            speak("Sorry, I encountered an error. Please repeat.")


if __name__ == "__main__":
    main()
