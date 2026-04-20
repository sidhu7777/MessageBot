import os
import tempfile
import time
import unittest
import wave
from pathlib import Path


RUN_ENV = "RUN_LIVE_WHISPER_MIC_TEST"
MODEL_ENV = "WHISPER_MODEL"
DURATION_ENV = "MIC_RECORD_SECONDS"
EXPECTED_ENV = "EXPECTED_TRANSCRIPT_SNIPPET"
SAMPLE_RATE = 16_000


class TestLiveWhisperMicCpu(unittest.TestCase):
    def test_system_mic_to_whisper_cpu(self) -> None:
        if os.getenv(RUN_ENV, "0") != "1":
            self.skipTest(
                f"Set {RUN_ENV}=1 to run the live microphone Whisper CPU test."
            )

        sounddevice = self._import_or_skip("sounddevice")
        np = self._import_or_skip("numpy")
        whisper = self._import_or_skip("whisper")

        duration_seconds = int(os.getenv(DURATION_ENV, "6"))
        model_name = os.getenv(MODEL_ENV, "tiny")
        expected_snippet = os.getenv(EXPECTED_ENV, "").strip().lower()

        print(
            f"\nRecording from system mic for {duration_seconds} seconds "
            f"at {SAMPLE_RATE} Hz. Speak now.",
            flush=True,
        )
        time.sleep(0.5)

        audio = sounddevice.rec(
            int(duration_seconds * SAMPLE_RATE),
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
        )
        sounddevice.wait()

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            wav_path = Path(handle.name)

        try:
            self._write_wav_file(wav_path, audio, np)

            load_started = time.perf_counter()
            model = whisper.load_model(model_name, device="cpu")
            load_seconds = round(time.perf_counter() - load_started, 3)

            transcribe_started = time.perf_counter()
            result = model.transcribe(
                str(wav_path),
                language="en",
                fp16=False,
                verbose=False,
            )
            transcribe_seconds = round(time.perf_counter() - transcribe_started, 3)
        finally:
            if wav_path.exists():
                wav_path.unlink()

        transcript = (result.get("text") or "").strip()
        normalized = " ".join(transcript.lower().split())

        print(f"Loaded Whisper model '{model_name}' on CPU in {load_seconds}s", flush=True)
        print(f"Transcription completed in {transcribe_seconds}s", flush=True)
        print(f"Transcript: {transcript}", flush=True)

        self.assertTrue(
            transcript,
            "Whisper returned empty text. Check mic access, recording quality, or dependencies.",
        )

        if expected_snippet:
            self.assertIn(
                expected_snippet,
                normalized,
                f"Expected transcript snippet '{expected_snippet}' was not found in '{normalized}'.",
            )

    def _import_or_skip(self, module_name: str):
        try:
            return __import__(module_name)
        except ImportError as exc:
            self.skipTest(
                f"Missing optional dependency '{module_name}': {exc}. "
                "Install it before running this live test."
            )

    def _write_wav_file(self, wav_path: Path, audio, np) -> None:
        pcm_audio = np.asarray(audio).reshape(-1)
        with wave.open(str(wav_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm_audio.tobytes())


if __name__ == "__main__":
    unittest.main()
