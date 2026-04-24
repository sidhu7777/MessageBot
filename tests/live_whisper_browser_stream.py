import os
import json
import asyncio
import importlib.util
import re
import subprocess
import glob
import site
import shutil
import tempfile
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from dotenv import load_dotenv
from starlette.concurrency import run_in_threadpool


def _load_test_llm_client():
    module_path = Path(__file__).resolve().parents[1] / "src" / "llm" / "client.py"
    spec = importlib.util.spec_from_file_location("tests._live_whisper_llm_client", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load LLM client module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.LLMClient


LLMClient = _load_test_llm_client()

load_dotenv()


MODEL_NAME = os.getenv("WHISPER_MODEL", "large-v3-turbo")
LANGUAGE = os.getenv("WHISPER_LANGUAGE", "en")
LLM_MODEL_NAME = os.getenv("LLM_MODEL_NAME", "qwen3:1.7b").strip() or "qwen3:1.7b"
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
WHISPER_DEVICE = os.getenv("WHISPER_DEVICE", "auto").strip().lower() or "auto"
WHISPER_COMPUTE_TYPE = os.getenv("WHISPER_COMPUTE_TYPE", "").strip().lower()


LLM_TIMEOUT_SECONDS = max(10.0, float(os.getenv("WHISPER_LLM_TIMEOUT_SECONDS", "45.0")))
USE_LLM_CLEANER = os.getenv("WHISPER_USE_LLM_CLEANER", "false").strip().lower() in {"1", "true", "yes", "on"}
USE_LLM_STRUCTURER = os.getenv("WHISPER_USE_LLM_STRUCTURER", "false").strip().lower() in {"1", "true", "yes", "on"}
WHISPER_REQUEST_TIMEOUT_SECONDS = max(
    10.0, float(os.getenv("WHISPER_REQUEST_TIMEOUT_SECONDS", "60.0"))
)
CLEAN_REQUEST_TIMEOUT_SECONDS = max(
    10.0, float(os.getenv("WHISPER_CLEAN_REQUEST_TIMEOUT_SECONDS", str(LLM_TIMEOUT_SECONDS + 30.0)))
)
PARCHI_REQUEST_TIMEOUT_SECONDS = max(
    10.0, float(os.getenv("WHISPER_PARCHI_REQUEST_TIMEOUT_SECONDS", str(LLM_TIMEOUT_SECONDS + 30.0)))
)
CHUNK_SECONDS = max(2.0, float(os.getenv("WHISPER_CHUNK_SECONDS", "8.0")))
CHUNK_MS = int(CHUNK_SECONDS * 1000)
MAX_SEGMENT_SECONDS = max(CHUNK_SECONDS, float(os.getenv("WHISPER_MAX_SEGMENT_SECONDS", "14.0")))
MIN_SPEECH_SECONDS = max(0.4, float(os.getenv("WHISPER_MIN_SPEECH_SECONDS", "1.2")))
SILENCE_SECONDS = max(0.3, float(os.getenv("WHISPER_SILENCE_SECONDS", "2.0")))
VAD_THRESHOLD = max(0.003, float(os.getenv("WHISPER_VAD_THRESHOLD", "0.035")))
SPEECH_START_SECONDS = max(0.1, float(os.getenv("WHISPER_SPEECH_START_SECONDS", "0.25")))
PROMPT_TAIL_CHARS = max(80, int(os.getenv("WHISPER_PROMPT_TAIL_CHARS", "180")))
TARGET_SAMPLE_RATE = int(os.getenv("WHISPER_TARGET_SAMPLE_RATE", "16000"))
WHISPER_NO_SPEECH_THRESHOLD = float(os.getenv("WHISPER_NO_SPEECH_THRESHOLD", "0.7"))
WHISPER_VAD_MIN_SILENCE_MS = int(os.getenv("WHISPER_VAD_MIN_SILENCE_MS", "500"))
WHISPER_VAD_SPEECH_PAD_MS = int(os.getenv("WHISPER_VAD_SPEECH_PAD_MS", "400"))
WHISPER_VAD_FILTER_THRESHOLD = float(os.getenv("WHISPER_VAD_FILTER_THRESHOLD", "0.3"))
COMMON_MEDICINE_TERMS = [
    "Dolo 650", "Dolo", "Crocin 650", "Crocin 500", "Crocin", "Calpol", "Paracetamol",
    "Pantop 40", "Pantop", "Pantoprazole", "Nexpro 40", "Nexpro", "Nexium",
    "Rablet 20", "Rablet", "Rabeprazole", "Omeprazole", "Domperidone",
    "Azithral 500", "Azithral", "Azithromycin", "Augmentin 625", "Augmentin",
    "Amoxicillin", "Amoxycillin", "Ciprofloxacin", "Cetrizine", "Cetirizine",
    "Telmisartan 40", "Telmisartan", "Telma", "Amlodipine", "Metformin 500",
    "Metformin", "Atorvastatin 10", "Atorvastatin", "Rosuvastatin", "Ecosprin",
    "Montair", "Montek", "Levocetirizine", "Allegra", "Pan-D", "Pan D",
    "Norflox", "Ofloxacin", "Ondem", "Ondansetron", "Brufen", "Brofin",
]
DEFAULT_MEDICAL_PROMPT = (
    "Doctor prescription dictation in Indian clinic English. "
    "Medicine names and common ASR-sensitive terms: "
    + ", ".join(COMMON_MEDICINE_TERMS)
    + ". Frequencies: OD, BD, TDS, QID, once daily, twice daily, thrice daily. "
    + "Timings: morning, afternoon, evening, night, before food, after food, empty stomach. "
    + "Vitals: BP, blood pressure, temperature, weight. Follow-up after 3 days, 5 days, 1 week. "
    + "These are vocabulary hints only; do not add a medicine unless it is spoken."
)
MEDICAL_PROMPT = os.getenv("WHISPER_MEDICAL_PROMPT", DEFAULT_MEDICAL_PROMPT).strip()
TRIM_SILENCE_THRESHOLD = max(0.0015, float(os.getenv("WHISPER_TRIM_SILENCE_THRESHOLD", "0.003")))
MIN_CHUNK_RMS = max(50, int(os.getenv("WHISPER_MIN_CHUNK_RMS", "350")))
MIN_SPEECH_RATIO = max(0.05, float(os.getenv("WHISPER_MIN_SPEECH_RATIO", "0.22")))
DEDUPE_SIMILARITY = max(0.7, float(os.getenv("WHISPER_DEDUPE_SIMILARITY", "0.98")))
MIN_ACCEPTED_WORDS = max(2, int(os.getenv("WHISPER_MIN_ACCEPTED_WORDS", "3")))
SAVE_AUDIO_DIR = Path(
    os.getenv("WHISPER_SAVE_AUDIO_DIR", "tests/artifacts/live_whisper_audio")
).resolve()

app = FastAPI(title="Live Whisper Browser Stream Test")
_model = None
_model_lock = threading.Lock()
_session_store: Dict[str, List[str]] = {}
_chunk_counts: Dict[str, int] = {}
_session_prompt_tail: Dict[str, str] = {}
_session_medicine_vocab: Dict[str, Set[str]] = {}
_last_chunk_text: Dict[str, str] = {}
_session_cleaned_text: Dict[str, str] = {}
_session_cleaned_raw: Dict[str, str] = {}
_llm_client = LLMClient(
    model=LLM_MODEL_NAME,
    provider="ollama",
    base_url=OLLAMA_BASE_URL,
    timeout_seconds=LLM_TIMEOUT_SECONDS,
)
_llm_clean_lock = threading.Lock()
_llm_structure_lock = threading.Lock()
_llm_frequency_lock = threading.Lock()

MEDICAL_KEYWORDS = {
    "tablet", "capsule", "capsules", "syrup", "injection", "medicine", "medicines",
    "prescription", "follow-up", "followup", "review", "days", "day", "weeks", "week",
    "months", "month", "paracetamol", "dolo", "pantop", "nexpro", "rablet", "azithral",
    "augmentin", "amoxicillin", "ibuprofen", "aspirin", "metformin", "atorvastatin",
    "omeprazole", "pantoprazole", "azithromycin", "ciprofloxacin", "nexium", "rabeprazole",
    "esomeprazole", "lansoprazole", "domperidone", "patient", "complaint", "complained",
    "acidity", "fever", "headache", "cough", "cold", "pain", "stomach", "diagnosis",
    "symptoms", "treatment", "dosage", "frequency", "timing", "duration", "morning",
    "afternoon", "evening", "night", "before", "after", "food", "empty", "stomach",
    "twice", "thrice", "daily", "weekly", "monthly"
}
MEDICAL_KEYWORDS.update(
    term.lower()
    for name in COMMON_MEDICINE_TERMS
    for term in re.findall(r"[A-Za-z][A-Za-z0-9-]*", name)
)


@app.on_event("startup")
async def log_runtime_configuration() -> None:
    actual_device, actual_compute = _whisper_runtime_config()
    print(
        "Live Whisper test runtime: "
        f"python={os.sys.version.split()[0]} "
        f"whisper_model={MODEL_NAME} "
        f"whisper_requested={WHISPER_DEVICE} "
        f"whisper_actual={actual_device} "
        f"whisper_compute={actual_compute} "
        f"llm_model={LLM_MODEL_NAME} "
        f"ollama_base_url={OLLAMA_BASE_URL}"
    )


def _ensure_cuda_user_libs_in_env() -> None:
    # Add pip-installed CUDA runtime directories (site-packages/nvidia/*/lib).
    candidate_dirs: List[str] = []
    try:
        for base in site.getsitepackages():
            candidate_dirs.extend(glob.glob(os.path.join(base, "nvidia", "*", "lib")))
    except Exception:
        return
    existing = os.environ.get("LD_LIBRARY_PATH", "")
    current = [p for p in existing.split(":") if p]
    for lib_dir in candidate_dirs:
        if os.path.isdir(lib_dir) and lib_dir not in current:
            current.insert(0, lib_dir)
    if current:
        os.environ["LD_LIBRARY_PATH"] = ":".join(current)


def _ensure_nvidia_device_nodes() -> None:
    # Some VM/container setups load NVIDIA kernel modules but miss /dev/nvidia* nodes.
    try:
        subprocess.run(["nvidia-modprobe", "-u", "-c=0"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass
    # Best-effort fallback for root sessions.
    required_nodes = [
        ("/dev/nvidiactl", 195, 255),
        ("/dev/nvidia0", 195, 0),
        ("/dev/nvidia-modeset", 195, 254),
        ("/dev/nvidia-uvm", 235, 0),
        ("/dev/nvidia-uvm-tools", 235, 1),
    ]
    for path, major, minor in required_nodes:
        if os.path.exists(path):
            continue
        try:
            os.mknod(path, 0o666 | 0o20000, os.makedev(major, minor))
        except Exception:
            continue


def _cuda_available() -> bool:
    _ensure_cuda_user_libs_in_env()
    _ensure_nvidia_device_nodes()
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:
        return False


def _whisper_runtime_config() -> Tuple[str, str]:
    requested = WHISPER_DEVICE
    if requested == "cpu":
        return "cpu", WHISPER_COMPUTE_TYPE or "int8"

    if requested in {"auto", "cuda", "gpu"} and _cuda_available():
        return "cuda", WHISPER_COMPUTE_TYPE or "float16"

    if requested in {"cuda", "gpu"}:
        print("WHISPER_DEVICE requested GPU but CUDA is unavailable; falling back to CPU.")
    return "cpu", WHISPER_COMPUTE_TYPE or "int8"


def _load_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                from faster_whisper import WhisperModel
                device, compute_type = _whisper_runtime_config()
                print(
                    f"Loading WhisperModel model={MODEL_NAME} device={device} compute_type={compute_type}"
                )

                _model = WhisperModel(
                    MODEL_NAME,
                    device=device,
                    compute_type=compute_type,
                )
    return _model


def _normalize_text(text: str) -> str:
    return " ".join(text.split()).strip()


def _strip_code_fences(text: str) -> str:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines:
            lines = lines[1:]
        while lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    return raw


def _extract_json_object(text: str) -> Dict[str, Any]:
    """Extract valid JSON object from LLM output, handling medicines array specially."""
    cleaned = _strip_code_fences(text)
    
    # Try direct JSON parse first
    try:
        payload = json.loads(cleaned)
        if isinstance(payload, dict):
            # If medicines is missing but we have partial medicine data, wrap it
            if "medicines" not in payload and "name" in payload:
                payload = {"medicines": [payload]}
            return payload
    except Exception:
        pass
    
    # Try to extract valid JSON object
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        try:
            potential = cleaned[start : end + 1]
            payload = json.loads(potential)
            if isinstance(payload, dict):
                if "medicines" not in payload and "name" in payload:
                    payload = {"medicines": [payload]}
                return payload
        except Exception:
            pass
    
    # If we got here and text contains multiple medicine objects, try to wrap them
    if cleaned.count("{") > 1 and "name" in cleaned:
        try:
            # Extract all medicine-like objects
            medicines = []
            current_obj = ""
            brace_count = 0
            for char in cleaned:
                if char == "{":
                    brace_count += 1
                    current_obj += char
                elif char == "}":
                    current_obj += char
                    brace_count -= 1
                    if brace_count == 0 and current_obj.strip():
                        try:
                            med = json.loads(current_obj)
                            if isinstance(med, dict) and "name" in med:
                                medicines.append(med)
                        except Exception:
                            pass
                        current_obj = ""
                elif brace_count > 0:
                    current_obj += char
            
            if medicines:
                return {"medicines": medicines}
        except Exception:
            pass
    
    return {}


def _coerce_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        parts: List[str] = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
            elif isinstance(item, dict):
                label = str(item.get("name") or item.get("type") or item.get("test") or item.get("title") or "").strip()
                notes = str(item.get("notes") or item.get("note") or "").strip()
                if label and notes:
                    parts.append(f"{label} ({notes})")
                elif label:
                    parts.append(label)
                elif notes:
                    parts.append(notes)
        return ", ".join(parts).strip()
    if isinstance(value, dict):
        parts = []
        for key in ("summary", "name", "type", "test", "notes", "note", "text"):
            text = str(value.get(key) or "").strip()
            if text:
                parts.append(text)
        return ", ".join(parts).strip()
    return str(value or "").strip()


def _deduplicate_raw(text: str) -> str:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    seen: List[str] = []
    for sentence in sentences:
        is_duplicate = any(
            SequenceMatcher(None, sentence.lower(), prev.lower()).ratio() > 0.97
            for prev in seen
        )
        if not is_duplicate:
            seen.append(sentence)
    return ". ".join(seen)


def _strip_repetition_noise(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return ""

    cleaned = normalized
    noise_tail = re.search(
        r"\b(oh|uh|um|hmm|huh|aaa+|mmm+)\b(?:[\s,.;:!?]+\1\b){4,}.*$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if noise_tail:
        cleaned = cleaned[:noise_tail.start()].strip(" ,.;:")

    repeated_phrase = re.search(
        r"\b([A-Za-z][A-Za-z-]*(?:\s+\d{1,4})?)\b(?:[\s,.;:!?]+\1\b){4,}.*$",
        cleaned,
        flags=re.IGNORECASE,
    )
    if repeated_phrase:
        cleaned = cleaned[:repeated_phrase.start()].strip(" ,.;:")

    # If hallucinated medicine spam started after a valid follow-up sentence, remove
    # the orphan medicine fragment immediately before the repeated tail.
    cleaned = re.sub(
        r"(?i)\b(Follow-up:\s*\d+\s+(?:day|days|week|weeks|month|months))[\s.!,;:]+[A-Z][A-Za-z-]*(?:\s+\d{1,4})?\s*$",
        r"\1",
        cleaned,
    )
    return _normalize_text(cleaned)


def _extract_guard_tokens(text: str) -> List[str]:
    tokens = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9\-]*", text.lower())
    kept: List[str] = []
    for token in tokens:
        if any(ch.isdigit() for ch in token) or token in MEDICAL_KEYWORDS or len(token) >= 7:
            kept.append(token)
    return kept


def _normalize_followup_phrases(text: str) -> str:
    cleaned = re.sub(
        r"\b(?:i\s+told\s+(?:him|her|the\s+patient)\s+to\s+)?come\s+(?:back\s+)?after\s+(\d+)\s+(day|days|week|weeks|month|months)\b",
        r"Follow-up: \1 \2",
        text,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\bfollow\s*up\s*(?:after)?\s*(\d+)\s+(day|days|week|weeks|month|months)\b",
        r"Follow-up: \1 \2",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned


_SYMPTOM_WORDS = (
    r"vomiting|nausea|fever|cough|pain|headache|diarrhea|diarrhoea|"
    r"fatigue|weakness|dizziness|chills|breathlessness|constipation|"
    r"bloating|burning|itching|rash|swelling"
)


def _apply_final_term_corrections(text: str) -> str:
    corrected = str(text or "")
    for final_value, wrong_value in re.findall(
        r"\b(?:it'?s|it\s+is)?\s*([A-Za-z][A-Za-z0-9-]{1,})\s+not\s+([A-Za-z][A-Za-z0-9-]{1,})\b",
        corrected,
        flags=re.IGNORECASE,
    ):
        final_clean = final_value.strip()
        wrong_clean = wrong_value.strip()
        if (
            not final_clean
            or not wrong_clean
            or final_clean.lower() == wrong_clean.lower()
            or final_clean.lower() in {"is", "it", "not", "a", "the"}
        ):
            continue
        corrected = re.sub(
            rf"\b{re.escape(wrong_clean)}\b",
            final_clean,
            corrected,
            flags=re.IGNORECASE,
        )
    return corrected


def _remove_noop_corrections(text: str) -> str:
    cleaned = str(text or "")
    cleaned = re.sub(
        r"\b(?:it'?s|it\s+is)\s+not\s+(?:a\s+)?([A-Za-z][A-Za-z0-9-]*)\s*,?\s*(?:it\s+is\s+)?(?:a\s+)?\1\b\.?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"\b(?:it'?s|it\s+is)?\s*([A-Za-z][A-Za-z0-9-]*)\s+not\s+\1\b\.?",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return _normalize_text(cleaned)


def _normalize_medical_asr_terms(text: str) -> str:
    normalized = str(text or "")
    replacements = [
        (r"\bwrap\s*,?\s*let\b", "Rablet"),
        (r"\bwraplet\b", "Rablet"),
        (r"\braplet\b", "Rablet"),
        (r"\bnext\s+probe\b", "Nexpro"),
        (r"\bnextpro\b", "Nexpro"),
        (r"\btells?\s+me\s+sartan\b", "Telmisartan"),
        (r"\btell\s+me\s+sartan\b", "Telmisartan"),
        (r"\btelma\s+sartan\b", "Telmisartan"),
        (r"\bblood\s+pleasure\b", "blood pressure"),
    ]
    for pattern, replacement in replacements:
        normalized = re.sub(pattern, replacement, normalized, flags=re.IGNORECASE)
    return normalized


def _resolve_self_corrections(text: str) -> str:
    corrected = _normalize_medical_asr_terms(str(text or ""))
    corrected = _remove_noop_corrections(corrected)
    corrected = _apply_final_term_corrections(corrected)
    corrected = _remove_noop_corrections(corrected)
    # Remove wrong frequency when corrected
    corrected = re.sub(
        r"\bevery\s+\d+\s+days[^.]*\.\s*(?:not|no)[^.]*\.\s*(?:it\s+is\s+)?([a-z\s]+(?:daily|times))",
        r"\1",
        corrected,
        flags=re.IGNORECASE,
    )
    corrected = re.sub(
        r"\bno[,.]?\s+no[,.]?\s+it\s+is\s+not\s+[^,.]+(?:,\s*|\.\s*)it\s+is\s+",
        "",
        corrected,
        flags=re.IGNORECASE,
    )
    corrected = re.sub(
        r"\bnot\s+[^,.]+(?:,\s*)it\s+is\s+",
        "",
        corrected,
        flags=re.IGNORECASE,
    )
    corrected = re.sub(
        r"\bi\s+mean\s+",
        "",
        corrected,
        flags=re.IGNORECASE,
    )
    corrected = re.sub(
        r"(?<=[.!?])\s*it\s+is\s+(?=" + _SYMPTOM_WORDS + r"\b)",
        "",
        corrected,
        flags=re.IGNORECASE,
    )
    corrected = re.sub(
        r"\b(" + _SYMPTOM_WORDS + r")\s+in\s+(" + _SYMPTOM_WORDS + r")\b",
        r"\1 and \2",
        corrected,
        flags=re.IGNORECASE,
    )
    return _normalize_text(corrected)


def _basic_medical_cleanup(raw_text: str) -> str:
    cleaned = _normalize_text(_normalize_followup_phrases(_deduplicate_raw(raw_text)))
    cleaned = _resolve_self_corrections(cleaned)
    cleaned = _normalize_text(_strip_repetition_noise(cleaned))
    if not cleaned:
        return ""
    cleaned = re.sub(r"\b(?:thank you|thanks|thank you for watching)\b\.?", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\b(?:for|to)\s+this\s+patient\s+i\s+will\s+give\s+(?:him|her)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bi\s+will\s+give\s+(?:him|her)\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\ballow\s+a\s+given\s+patient\b[:,]?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bi\s+told\s+(?:him|her|the\s+patient)\s+to\s+", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"(?i)\.\s+and\s+(Follow-up:)", r". \1", cleaned)
    cleaned = re.sub(r"(?i)\b(?:and\s*)+\.\s*", ". ", cleaned)
    cleaned = re.sub(r"(?i)(?:^|[.!?]\s*)(?:it'?s|it\s+is)\s*$", "", cleaned).strip()
    cleaned = re.sub(r"\.{2,}", ".", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip(" ,.;")
    parts = [part.strip(" ,.;") for part in re.split(r"(?i)\bFollow-up:\b", cleaned) if part.strip(" ,.;")]
    if len(parts) == 2:
        cleaned = f"{parts[0]}. Follow-up: {parts[1].strip(' ,.;')}"
    return _normalize_text(cleaned)


def _cleaning_output_is_safe(raw_text: str, cleaned_text: str) -> bool:
    raw = _normalize_text(raw_text)
    cleaned = _normalize_text(cleaned_text)

    if not cleaned:
        return False
    if raw and len(cleaned) > max(500, int(len(raw) * 2.5)):
        return False
    has_repetition_noise = bool(re.search(r"\b(\w+)\b(?:[\s,.;:!?]+\1\b){4,}", raw, flags=re.IGNORECASE))
    min_ratio = 0.2 if has_repetition_noise else 0.45
    if raw and len(cleaned) < max(5, int(len(raw) * min_ratio)):
        return False
    cleaned_lower = cleaned.lower()
    raw_lower = raw.lower()

    financial_terms = [r'\$', r'₹', r'\bdollar', r'\brupee', r'\bthousand', r'\bmillion', r'\bbillion', r'\bcost', r'\bprice', r'\bpay']
    for term in financial_terms:
        if not re.search(term, raw_lower) and re.search(term, cleaned_lower):
            return False
    return True


def _clean_transcript_with_llm(raw_text: str, session_id: str = "") -> Tuple[str, float]:
    deduplicated = _strip_repetition_noise(_normalize_followup_phrases(_deduplicate_raw(raw_text)))
    resolved = _resolve_self_corrections(deduplicated)
    normalized = _normalize_text(resolved)
    if not normalized:
        return "", 0.0
    if not USE_LLM_CLEANER:
        return _basic_medical_cleanup(normalized), 0.0
    prompt_hint = _prompt_tail_for_session(session_id) if session_id else MEDICAL_PROMPT
    previous_cleaned = _normalize_text(_session_cleaned_text.get(session_id, "")) if session_id else ""
    system_prompt = (
        "Clean raw doctor dictation conservatively for a prescription draft. "
        "When the doctor self-corrects using phrases like 'no', 'not X, it is Y', or 'I mean', "
        "keep only the final corrected version and drop contradicted earlier wording. "
        "Fix ASR errors where symptom lists use 'in' instead of 'and'. "
        "Remove exact repetition only. "
        "Never invent billing, currency, money, prices, or quantities that are not clearly spoken. "
        "Never rewrite medicine-like words into money words. "
        "Never drop medicine names, strengths, frequencies, durations, or follow-up details. "
        "If a medicine name or dose is unclear, preserve the original wording instead of guessing. "
        "Convert 'come after X days' to 'Follow-up: X days'. "
        "Return only cleaned text, no explanation."
    )
    user_prompt = (
        "Medical context and likely terms:\n"
        f"{prompt_hint}\n\n"
        "Task:\n"
        "- Correct likely medical ASR mistakes.\n"
        "- Treat brand names, strengths, and numbers as high priority tokens.\n"
        "- Keep the meaning clinically plausible and preserve all prescription facts.\n"
        "- If the transcript repeats the same medicine in brand and generic form, keep the clearest single form.\n"
        "- If the doctor self-corrects, keep the final corrected statement only.\n"
        "- If a number is obviously a broken strength or frequency token, repair it only when the intended wording is clear from context.\n"
        "- Prefer the latest correction over earlier contradictory wording.\n"
        "- Keep medicine names and numbers exactly when uncertain.\n"
        "- Interpret symptom lists like 'vomiting in headache' as 'vomiting and headache' when clearly a list.\n"
        "- Never convert medicine or dosage words into currency or price phrases.\n"
        "- If the raw transcript is ambiguous, return a minimally cleaned version close to the raw text.\n\n"
        "Previous cleaned draft for this session:\n"
        f"{previous_cleaned or '(none)'}\n\n"
        "Raw Whisper transcript:\n"
        f"{normalized}"
    )
    started_at = time.perf_counter()
    with _llm_clean_lock:
        cleaned = _llm_client.generate(system_prompt, user_prompt)
    cleaned_text = _normalize_text(cleaned)
    if not _cleaning_output_is_safe(normalized, cleaned_text):
        print("LLM cleaning rejected by safety guard; returning normalized raw transcript.")
        cleaned_text = normalized
    cleaned_text = _basic_medical_cleanup(cleaned_text)
    return cleaned_text, time.perf_counter() - started_at


def _fallback_parchi_payload(cleaned_text: str) -> Dict[str, Any]:
    normalized = _normalize_text(cleaned_text)
    return {
        "patient_name": "",
        "complaints": "",
        "diagnosis": "",
        "medicines": [],
        "vital_signs": {
            "blood_pressure": "",
            "temperature": "",
            "weight": ""
        },
        "advice": normalized,
        "tests": "",
        "follow_up": "",
    }


def _blank_parchi_payload() -> Dict[str, Any]:
    return _fallback_parchi_payload("")


def _coerce_parchi_payload(payload: Dict[str, Any], transcript: str) -> Dict[str, Any]:
    result = _blank_parchi_payload()
    if not isinstance(payload, dict):
        return result

    result["patient_name"] = _coerce_text(payload.get("patient_name"))[:80]
    result["complaints"] = _coerce_text(payload.get("complaints"))[:300]
    result["diagnosis"] = _coerce_text(payload.get("diagnosis"))[:300]
    result["advice"] = _coerce_text(payload.get("advice"))[:500]
    result["tests"] = _coerce_text(payload.get("tests"))[:300]
    result["follow_up"] = _coerce_text(payload.get("follow_up"))[:80]

    vitals = payload.get("vital_signs")
    if isinstance(vitals, dict):
        result["vital_signs"] = {
            "blood_pressure": _coerce_text(vitals.get("blood_pressure"))[:40],
            "temperature": _coerce_text(vitals.get("temperature"))[:40],
            "weight": _coerce_text(vitals.get("weight"))[:40],
        }

    medicines = payload.get("medicines")
    clean_medicines: List[Dict[str, str]] = []
    if isinstance(medicines, list):
        for item in medicines[:12]:
            if not isinstance(item, dict):
                continue
            name = _coerce_text(item.get("name"))[:80]
            if not name:
                continue
            clean_medicines.append(
                {
                    "name": name,
                    "frequency": _coerce_text(item.get("frequency"))[:40],
                    "timing": _coerce_text(item.get("timing"))[:80],
                    "duration": _coerce_text(item.get("duration"))[:80],
                    "notes": _coerce_text(item.get("notes"))[:120],
                }
            )
    result["medicines"] = clean_medicines

    extracted_vitals = _extract_vital_signs(transcript)
    extracted_name = _extract_patient_name(transcript)
    extracted_complaints = _extract_complaints(transcript)
    extracted_diagnosis = _extract_diagnosis(transcript)
    extracted_tests = _extract_tests(transcript)
    if extracted_name:
        result["patient_name"] = extracted_name[:80]
    if extracted_complaints:
        result["complaints"] = extracted_complaints[:300]
    if extracted_diagnosis:
        result["diagnosis"] = extracted_diagnosis[:300]
    else:
        result["diagnosis"] = ""
    if extracted_tests:
        result["tests"] = extracted_tests[:300]
    if not result["follow_up"]:
        result["follow_up"] = _extract_follow_up(transcript)
    if not result["vital_signs"]["blood_pressure"]:
        result["vital_signs"]["blood_pressure"] = extracted_vitals["blood_pressure"]
    if not result["vital_signs"]["temperature"]:
        result["vital_signs"]["temperature"] = extracted_vitals["temperature"]
    if not result["vital_signs"]["weight"]:
        result["vital_signs"]["weight"] = extracted_vitals["weight"]
    return result


def _payload_has_clinical_content(payload: Dict[str, Any]) -> bool:
    return bool(
        _coerce_text(payload.get("patient_name"))
        or _coerce_text(payload.get("complaints"))
        or _coerce_text(payload.get("diagnosis"))
        or payload.get("medicines")
        or any(_coerce_text(v) for v in dict(payload.get("vital_signs") or {}).values())
        or _coerce_text(payload.get("follow_up"))
    )


def _build_parchi_payload_with_llm(cleaned_text: str) -> Tuple[Dict[str, Any], float]:
    normalized = _normalize_text(cleaned_text)
    if not normalized:
        return {}, 0.0

    system_prompt = (
        "You convert noisy doctor dictation into prescription JSON. "
        "Return only valid JSON. Do not add medicines, strengths, vitals, tests, diagnosis, or advice unless supported by the transcript. "
        "Use final corrections when the doctor says 'X not Y' or 'not X, it is Y'. "
        "Infer common medical ASR spellings when clear from context, but leave uncertain text as spoken."
    )
    user_prompt = (
        "Extract this exact JSON schema:\n"
        "{\n"
        '  "patient_name": "",\n'
        '  "complaints": "",\n'
        '  "diagnosis": "",\n'
        '  "medicines": [{"name": "", "frequency": "", "timing": "", "duration": "", "notes": ""}],\n'
        '  "vital_signs": {"blood_pressure": "", "temperature": "", "weight": ""},\n'
        '  "advice": "",\n'
        '  "tests": "",\n'
        '  "follow_up": ""\n'
        "}\n\n"
        "Rules:\n"
        "- Keep medicine names without inventing dose numbers.\n"
        "- Put symptoms in complaints.\n"
        "- Put confirmed disease labels like high blood pressure in diagnosis.\n"
        "- Put BP/temp/weight only in vital_signs.\n"
        "- Put 'come after N days' as follow_up.\n"
        "- If a field is not present, use an empty string or empty list.\n\n"
        f"Transcript:\n{normalized}"
    )
    started_at = time.perf_counter()
    with _llm_structure_lock:
        raw = _llm_client.generate(system_prompt, user_prompt)
    payload = _extract_json_object(raw)
    coerced = _coerce_parchi_payload(payload, normalized)
    if not _payload_has_clinical_content(coerced):
        raise ValueError("LLM parchi extraction returned empty payload")
    return coerced, time.perf_counter() - started_at


# ============================================================================
# HYBRID ARCHITECTURE: Rules (80%) + LLM Disambiguation (20%)
# ============================================================================

def _extract_medicine_names(text: str) -> List[str]:
    """Extract medicine names using production-level rules."""
    medicines = []
    cleaned_text = _strip_repetition_noise(text)
    if not cleaned_text:
        return []
    text = cleaned_text
    
    # Pattern 1: Brand names with numbers (Dolo 650, Crocin 500, Pantop 40)
    pattern1 = re.findall(r'\b([A-Z][a-z]+(?:-[A-Z])?)\s+(\d+)\b', text)
    for name, strength in pattern1:
        medicines.append(f"{name} {strength}")
    
    # Pattern 2: Medicine name patterns (flexible matching)
    # This catches variations like "pantop", "nexpro", "rablet" regardless of context
    medicine_patterns = [
        r'\b(paracetamol|dolo|crocin)\b',
        r'\b(pantop|pantoprazole)\b', 
        r'\b(nexpro|nexium|esomeprazole)\b',
        r'\b(rablet|rablette|rabeprazole)\b',
        r'\b(telmisartan|telma)\b',
        r'\b(azithral|azithromycin)\b',
        r'\b(augmentin|amoxicillin)\b',
        r'\b(ibuprofen|aspirin|metformin|atorvastatin)\b',
        r'\b(omeprazole|ciprofloxacin|lansoprazole|domperidone)\b'
    ]
    
    for pattern in medicine_patterns:
        matches = re.findall(pattern, text, re.I)
        for match in matches:
            # Look for strength/dosage nearby
            context_match = re.search(rf'\b({re.escape(match)}(?:\s+\d+)?)\b', text, re.I)
            if context_match:
                medicines.append(context_match.group(1).title())
            else:
                medicines.append(match.title())
    
    # Pattern 3: Generic medicine-like words (ending in common suffixes)
    generic_pattern = r'\b([A-Z][a-z]{3,}(?:ol|in|ine|ate|ide|ium))\b'
    generic_matches = re.findall(generic_pattern, text)
    for match in generic_matches:
        if len(match) >= 5:  # Minimum length for medicine names
            medicines.append(match)
    
    aliases = {
        "grofin": "Brofin",
        "brufen": "Brofin",
        "brofen": "Brofin",
        "wraplet": "Rablet",
        "raplet": "Rablet",
        "rablette": "Rablet",
        "rablettee": "Rablet",
        "next probe": "Nexpro",
        "nextpro": "Nexpro",
        "tell me sartan": "Telmisartan",
        "tells me sartan": "Telmisartan",
        "telma": "Telmisartan",
    }
    strongest: Dict[str, str] = {}
    unique: List[str] = []
    for med in medicines:
        normalized = _normalize_text(med).title()
        normalized = aliases.get(normalized.lower(), normalized)
        key = normalized.lower()
        base = re.sub(r"\s+\d{1,4}$", "", key).strip()
        if not key:
            continue
        existing = strongest.get(base)
        if existing is None or (re.search(r"\d", normalized) and not re.search(r"\d", existing)):
            strongest[base] = normalized
    seen = set()
    ordered_unique: List[Tuple[int, str]] = []
    for med in medicines:
        normalized = aliases.get(_normalize_text(med).title().lower(), _normalize_text(med).title())
        base = re.sub(r"\s+\d{1,4}$", "", normalized.lower()).strip()
        chosen = strongest.get(base, normalized)
        key = chosen.lower()
        if key and key not in seen:
            seen.add(key)
            position = text.lower().find(key)
            if position < 0:
                position = text.lower().find(base)
            ordered_unique.append((position if position >= 0 else 10_000 + len(ordered_unique), chosen))
    for _position, chosen in sorted(ordered_unique, key=lambda item: item[0]):
        unique.append(chosen)
    return unique


def _extract_patient_name(text: str) -> str:
    """Extract patient name using rules."""
    blocked_tokens = {
        "complained",
        "complaint",
        "complaining",
        "has",
        "having",
        "gave",
        "fever",
        "cough",
        "pain",
        "acidity",
        "temperature",
        "weight",
        "follow",
        "followup",
        "days",
        "day",
    }
    explicit_patterns = [
        r"\bpatient'?s\s+name\s+is\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and|he|she|who|with|complained|complaint)\b|[,.]|$)",
        r"\b(?:his|her|the\s+patient)\s+name\s+is\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and|he|she|who|with|complained|complaint)\b|[,.]|$)",
        r"\bpatient\s+name\s+is\s+([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and|he|she|who|with|complained|complaint)\b|[,.]|$)",
        r"\bpatient\s+name\s+(?!is\b)([A-Za-z]+(?:\s+[A-Za-z]+){0,2}?)(?=\s+(?:and|he|she|who|with|complained|complaint)\b|[,.]|$)",
    ]

    candidates: List[str] = []
    for pattern in explicit_patterns:
        for match in re.finditer(pattern, text, re.I):
            candidate = " ".join(match.group(1).strip().split()).title()
            words = candidate.lower().split()
            if candidate and not any(word in blocked_tokens for word in words):
                candidates.append(candidate)

    if not candidates:
        match = re.search(
            r"\bpatient\s+(?!complained\b|complaint\b|complaining\b|has\b|having\b)([A-Za-z]+)\b",
            text,
            re.I,
        )
        if match:
            candidate = match.group(1).strip().title()
            if candidate.lower() not in blocked_tokens:
                return candidate
        return ""

    chosen = candidates[-1]
    if len(chosen.split()) == 1:
        for previous in reversed(candidates[:-1]):
            previous_parts = previous.split()
            if len(previous_parts) >= 2:
                chosen = " ".join([chosen] + previous_parts[1:])
                break
    return chosen


def _extract_complaints(text: str) -> str:
    """Extract complaints/symptoms using improved rules."""
    # More flexible patterns to handle various speech structures
    patterns = [
        # "complained about he has X" -> extract X
        r'complained?\s+about\s+(?:he\s+has\s+|she\s+has\s+|having\s+|that\s+he\s+has\s+|that\s+she\s+has\s+)?([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)',
        # "complaining of X" 
        r'complaining\s+of\s+([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)',
        # "he has X" or "she has X"
        r'(?:he|she|patient)\s+has\s+([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)',
        # "suffering from X"
        r'suffering\s+from\s+([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)',
        # "problem with X" or "issue with X"
        r'(?:problem|issue)\s+with\s+([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)',
        # "symptoms of X" or "symptom of X"
        r'symptoms?\s+of\s+([A-Za-z\s]+?)(?=\s+(?:so|and)\b|[,.]|$)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            complaint = match.group(1).strip()
            # Clean up common filler words
            complaint = re.sub(r'\b(the|a|an|some|any)\b', '', complaint, flags=re.I).strip()
            if complaint and len(complaint) > 2:
                if re.fullmatch(r"[A-Z0-9]{2,6}", complaint.strip()):
                    return complaint.strip().upper()
                return complaint.title()
    
    return ""


def _extract_diagnosis(text: str) -> str:
    patterns = [
        r"\bdiagnosis\s*(?:is|was|:)?\s*([A-Za-z][A-Za-z\s-]{1,80})",
        r"\bdiagnosed\s+with\s+([A-Za-z][A-Za-z\s-]{1,80})",
        r"\bdiagnose\s+(?:it\s+)?as\s+([A-Za-z][A-Za-z\s-]{1,80})",
        r"\bimpression\s*(?:is|was|:)?\s*([A-Za-z][A-Za-z\s-]{1,80})",
        r"\bassessment\s*(?:is|was|:)?\s*([A-Za-z][A-Za-z\s-]{1,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            diagnosis = match.group(1).strip(" ,.;:-")
            diagnosis = re.split(r"(?i)\b(?:and|with|so|given|prescribed|follow-up|follow up)\b|[,.]", diagnosis)[0].strip()
            if diagnosis:
                return diagnosis.title()
    return ""


def _extract_vital_signs(text: str) -> Dict[str, str]:
    vitals = {
        "blood_pressure": "",
        "temperature": "",
        "weight": "",
    }
    normalized = _normalize_text(text)
    normalized = re.sub(r"(?i)\b(?:bp|blood\s+pressure)(?:\s*,\s*(?:bp|blood\s+pressure)){1,}\s*[,.:]?\s*", "BP ", normalized)
    normalized = re.sub(r"(?i)\btemperature\s*[,.:]\s*", "temperature ", normalized)
    normalized = re.sub(r"(?i)\bweight\s*[,.:]\s*", "weight ", normalized)
    bp_match = re.search(
        r"\b(?:bp|blood\s+pressure)\s*(?:is|was|:)?\s*(\d{2,3})\s*(?:/|by|over)\s*(\d{2,3})\b",
        normalized,
        re.I,
    )
    if bp_match:
        vitals["blood_pressure"] = f"{bp_match.group(1)}/{bp_match.group(2)} mmHg"

    temp_match = re.search(
        r"\b(?:temp|temperature)\s*(?:is|was|:)?\s*(\d{2,3}(?:\.\d+)?)\s*(?:degree|degrees|f|fahrenheit)?\b",
        normalized,
        re.I,
    )
    if temp_match:
        vitals["temperature"] = f"{temp_match.group(1)}°F"

    weight_match = re.search(
        r"\b(?:weight|wt)\s*(?:is|was|:)?\s*(\d{1,3}(?:\.\d+)?)\s*(?:kg|kgs|kilogram|kilograms)?\b",
        normalized,
        re.I,
    )
    if weight_match:
        vitals["weight"] = f"{weight_match.group(1)} kg"
    return vitals


def _extract_tests(text: str) -> str:
    patterns = [
        r"\b(?:recommend|recommended|advise|advised|suggest|suggested)\s+(?:recommended\s+)?tests?\s+(?:like|such\s+as|are|is|:)?\s*([A-Za-z][A-Za-z\s,]+)",
        r"\btests?\s+(?:like|such\s+as|are|is|:)\s*([A-Za-z][A-Za-z\s,]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            value = match.group(1).strip(" ,.;:-")
            value = re.split(r"(?i)\b(?:bp|temperature|weight|follow-up|follow up|diagnose|diagnosis)\b", value)[0].strip(" ,.;:-")
            value = re.sub(r"(?i)\b(?:and)\b", ", ", value)
            value = re.sub(r"\s*,\s*", ", ", value)
            value = re.sub(r"\s{2,}", " ", value).strip(" ,.;:-")
            if value:
                return value.title()
    return ""


def _extract_duration(text: str) -> str:
    """Extract duration using rules (high precision)."""
    # Medicine duration only, not follow-up phrases like "come after 5 days".
    match = re.search(
        r'\b(?:for|continue\s+for|take\s+for|give\s+for)\s+(\d+)\s+(day|days|week|weeks|month|months)\b',
        text,
        re.I,
    )
    if match:
        return f"{match.group(1)} {match.group(2).lower()}"
    return ""


def _extract_follow_up(text: str) -> str:
    """Extract follow-up independently from medicine duration."""
    match = re.search(r'\bFollow-up:\s*(\d+)\s+(day|days|week|weeks|month|months)\b', text, re.I)
    if match:
        return f"{match.group(1)} {match.group(2).lower()}"
    match = re.search(
        r'\bcome\s+(?:back\s+)?after\s+(\d+)\s+(day|days|week|weeks|month|months)\b',
        text,
        re.I,
    )
    if match:
        return f"{match.group(1)} {match.group(2).lower()}"
    return ""


def _extract_notes(text: str) -> str:
    """Extract notes using rules (high precision)."""
    text_lower = text.lower()
    if "before food" in text_lower or "before meal" in text_lower:
        return "Before food"
    if "after food" in text_lower or "after meal" in text_lower:
        return "After food"
    if "empty stomach" in text_lower:
        return "Empty stomach"
    return ""


def _extract_timing(text: str) -> str:
    """Extract timing using rules (high precision)."""
    text_lower = text.lower()
    
    # Check for combinations first (order matters!)
    if "morning" in text_lower and "afternoon" in text_lower and "evening" in text_lower and "night" in text_lower:
        return "Morning Afternoon Evening and Night"
    if "morning" in text_lower and "afternoon" in text_lower and "evening" in text_lower:
        return "Morning Afternoon and Evening"
    if "morning" in text_lower and "afternoon" in text_lower and "night" in text_lower:
        return "Morning Afternoon and Night"
    if "morning" in text_lower and "evening" in text_lower and "night" in text_lower:
        return "Morning Evening and Night"
    if "afternoon" in text_lower and "evening" in text_lower and "night" in text_lower:
        return "Afternoon Evening and Night"
    if "morning" in text_lower and "afternoon" in text_lower:
        return "Morning and Afternoon"
    if "morning" in text_lower and "evening" in text_lower:
        return "Morning and Evening"
    if "morning" in text_lower and "night" in text_lower:
        return "Morning and Night"
    if "afternoon" in text_lower and "evening" in text_lower:
        return "Afternoon and Evening"
    if "afternoon" in text_lower and "night" in text_lower:
        return "Afternoon and Night"
    if "evening" in text_lower and "night" in text_lower:
        return "Evening and Night"
    
    # Single timing
    if "morning" in text_lower:
        return "Morning"
    if "afternoon" in text_lower:
        return "Afternoon"
    if "evening" in text_lower:
        return "Evening"
    if "night" in text_lower:
        return "Night"
    
    return ""


def _extract_frequency_raw(text: str) -> str:
    """Extract frequency pattern (may be ambiguous)."""
    text_lower = text.lower()
    
    # Clear patterns (no LLM needed)
    if re.search(r'\bOD\b', text, re.I):
        return "OD"
    if re.search(r'\bBD\b', text, re.I):
        return "BD"
    if re.search(r'\bTDS\b', text, re.I):
        return "TDS"
    if re.search(r'\bQID\b', text, re.I):
        return "QID"
    if "once daily" in text_lower or "once a day" in text_lower:
        return "once daily"
    if "twice daily" in text_lower or "twice a day" in text_lower:
        return "twice daily"
    if "three times daily" in text_lower or "thrice daily" in text_lower:
        return "three times daily"
    if "four times daily" in text_lower:
        return "four times daily"
    
    # Ambiguous patterns (need LLM)
    if "daily twice" in text_lower or "twice" in text_lower:
        return f"AMBIGUOUS:{_context_window(text, 'twice')}"
    if "daily once" in text_lower or "once" in text_lower:
        return f"AMBIGUOUS:{_context_window(text, 'once')}"
    if "daily" in text_lower:
        return f"AMBIGUOUS:{_context_window(text, 'daily')}"
    
    return ""


def _context_window(text: str, needle: str, radius: int = 7) -> str:
    words = re.findall(r"\S+", text)
    lowered = [word.strip(".,;:!?").lower() for word in words]
    target = needle.lower()
    try:
        idx = lowered.index(target)
    except ValueError:
        return needle
    start = max(0, idx - radius)
    end = min(len(words), idx + radius + 1)
    return " ".join(words[start:end])


def _disambiguate_frequency_with_llm(ambiguous_text: str) -> str:
    """Use LLM ONLY for disambiguation (micro-task)."""
    system_prompt = (
        "You are a frequency classifier. Return ONLY ONE of these exact values:\n"
        "- OD\n"
        "- BD\n"
        "- TDS\n"
        "- QID\n"
        "Return ONLY the abbreviation, nothing else."
    )
    user_prompt = f"Classify this frequency: '{ambiguous_text}'"
    
    try:
        with _llm_frequency_lock:
            result = _llm_client.generate(system_prompt, user_prompt).strip().upper()
        
        # Validate result
        if result in ["OD", "BD", "TDS", "QID"]:
            return result
        
        # Fallback: keep original text
        return ambiguous_text
    except Exception as e:
        print(f"[LLM_DISAMBIGUATION] Failed: {e}")
        return ambiguous_text


def _build_rule_based_parchi_payload(cleaned_text: str) -> Tuple[Dict[str, Any], float]:
    """
    HYBRID ARCHITECTURE: Rules (80%) + LLM Disambiguation (20%)
    
    Step 1: Extract deterministic fields with RULES
    Step 2: Use LLM ONLY for ambiguous cases
    Step 3: Assemble JSON with CODE (not LLM)
    """
    normalized = _normalize_text(cleaned_text)
    if not normalized:
        return {}, 0.0
    
    if not re.search(r'\b(dolo|crocin|pantop|rablet|nexpro|telmisartan|ibuprofen|paracetamol|amoxycillin|azithromycin|mg|tablet|capsule|syrup|daily|twice|thrice|times|days?|weeks?|bp|blood\s+pressure)\b', normalized, re.I):
        print(f"[PARCHI_EXTRACTION] No medical signal detected in: {normalized}")
        return _fallback_parchi_payload(normalized), 0.0
    
    started_at = time.perf_counter()
    
    # ========================================================================
    # STEP 1: RULE-BASED EXTRACTION (80% of fields)
    # ========================================================================
    print(f"[PARCHI_EXTRACTION] Step 1: Rule-based extraction")
    
    # Extract medicine names
    medicine_names = _extract_medicine_names(normalized)
    
    # Extract patient name
    patient_name = _extract_patient_name(normalized)
    
    # Extract complaints
    complaints = _extract_complaints(normalized)

    # Extract diagnosis
    diagnosis = _extract_diagnosis(normalized)

    # Extract vital signs
    vital_signs = _extract_vital_signs(normalized)
    
    # Extract duration (deterministic)
    duration = _extract_duration(normalized)

    # Extract follow-up separately from medicine duration
    follow_up = _extract_follow_up(normalized)
    tests = _extract_tests(normalized)
    
    # Extract notes (deterministic)
    notes = _extract_notes(normalized)
    
    # Extract timing (deterministic)
    timing = _extract_timing(normalized)
    
    # Extract frequency (may be ambiguous)
    frequency_raw = _extract_frequency_raw(normalized)
    
    print(f"[PARCHI_EXTRACTION] Rules extracted: medicines={medicine_names}, duration={duration}, notes={notes}, timing={timing}, frequency_raw={frequency_raw}")
    
    # ========================================================================
    # STEP 2: LLM DISAMBIGUATION (20% - only for ambiguous cases)
    # ========================================================================
    frequency = frequency_raw
    if frequency_raw.startswith("AMBIGUOUS:"):
        print(f"[PARCHI_EXTRACTION] Step 2: LLM disambiguation for frequency")
        ambiguous_part = frequency_raw.split(":", 1)[1]
        frequency = _disambiguate_frequency_with_llm(ambiguous_part)
        print(f"[PARCHI_EXTRACTION] LLM disambiguated: {frequency_raw} → {frequency}")
    
    # ========================================================================
    # STEP 3: ASSEMBLE JSON (CODE, not LLM)
    # ========================================================================
    print(f"[PARCHI_EXTRACTION] Step 3: Assembling JSON")
    
    medicines = []
    if medicine_names:
        for med_name in medicine_names:
            medicines.append({
                "name": med_name,
                "frequency": frequency,
                "timing": timing,
                "duration": duration,
                "notes": notes,
            })
    
    payload = {
        "patient_name": patient_name,
        "complaints": complaints,
        "diagnosis": diagnosis,
        "medicines": medicines,
        "vital_signs": vital_signs,
        "advice": normalized if not medicines else "",
        "tests": tests,
        "follow_up": follow_up,
    }
    
    elapsed = time.perf_counter() - started_at
    print(f"[PARCHI_EXTRACTION] Final payload: {payload}")
    print(f"[PARCHI_EXTRACTION] Total time: {elapsed:.2f}s")
    
    return payload, elapsed


def _build_parchi_payload(cleaned_text: str) -> Tuple[Dict[str, Any], float]:
    normalized = _normalize_text(cleaned_text)
    if not normalized:
        return {}, 0.0
    if USE_LLM_STRUCTURER:
        try:
            payload, elapsed = _build_parchi_payload_with_llm(normalized)
            print(f"[PARCHI_EXTRACTION] LLM structured payload: {payload}")
            print(f"[PARCHI_EXTRACTION] Total time: {elapsed:.2f}s")
            return payload, elapsed
        except Exception as exc:
            print(f"[PARCHI_EXTRACTION] LLM structuring failed; using rules error={exc}")
    return _build_rule_based_parchi_payload(normalized)


def _clean_edit_field_value(field_name: str, raw_text: str, session_id: str = "") -> Tuple[str, float]:
    field = _normalize_text(str(field_name or "")).lower()
    normalized_raw = _normalize_text(raw_text)
    if not normalized_raw:
        return "", 0.0

    cleaned_text, elapsed = _clean_transcript_with_llm(normalized_raw, session_id)

    if field == "patient_name":
        value = _extract_patient_name(cleaned_text)
        if not value:
            value = re.sub(r"(?i)\b(?:patient'?s?|patient)\s+name\s+(?:is|was)?\s*", "", cleaned_text).strip(" ,.;:-")
        return value[:80], elapsed
    if field == "diagnosis":
        value = _extract_diagnosis(cleaned_text)
        if not value:
            value = re.sub(
                r"(?i)\b(?:diagnosis|assessment|impression)\s*(?:is|was|:)?\s*",
                "",
                cleaned_text,
            ).strip(" ,.;:-")
            value = re.split(r"[.,]", value)[0].strip()
        return value[:300], elapsed
    if field == "complaints":
        value = _extract_complaints(cleaned_text) or cleaned_text
        return value[:300], elapsed
    if field == "blood_pressure":
        return _extract_vital_signs(cleaned_text)["blood_pressure"][:40], elapsed
    if field == "temperature":
        return _extract_vital_signs(cleaned_text)["temperature"][:40], elapsed
    if field == "weight":
        return _extract_vital_signs(cleaned_text)["weight"][:40], elapsed
    if field == "follow_up":
        value = _extract_follow_up(cleaned_text) or cleaned_text
        return value[:80], elapsed
    if field == "tests":
        value = re.sub(r"(?i)\b(?:tests?|investigations?)\s*(?:are|is|:)?\s*", "", cleaned_text).strip(" ,.;:-")
        return value[:300], elapsed
    if field == "advice":
        value = re.sub(r"(?i)\b(?:advice|instructions?)\s*(?:are|is|:)?\s*", "", cleaned_text).strip(" ,.;:-")
        return value[:500], elapsed
    return cleaned_text[:500], elapsed


def _strip_repetitive_tail(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return normalized

    words = normalized.split()
    if len(words) < 6:
        return normalized

    lower_words = [w.strip(".,!?").lower() for w in words]
    repetitive_phrases = [
        ["thank", "you"],
        ["thank", "you", "very", "much"],
        ["bye"],
    ]
    cut_index = len(words)
    for phrase in repetitive_phrases:
        phrase_len = len(phrase)
        repeat_count = 0
        i = len(lower_words) - phrase_len
        while i >= 0 and lower_words[i:i + phrase_len] == phrase:
            repeat_count += 1
            i -= phrase_len
        if repeat_count >= 2:
            cut_index = min(cut_index, i + phrase_len + 1)

    cleaned = " ".join(words[:cut_index]).strip()
    return cleaned or normalized


def _collapse_repeated_phrases(text: str) -> str:
    normalized = _normalize_text(text)
    if not normalized:
        return normalized

    words = normalized.split()
    lower_words = [w.strip(".,!?").lower() for w in words]

    for phrase_len in range(3, min(12, max(3, len(words) // 2)) + 1):
        repeat_count = 1
        phrase = lower_words[:phrase_len]
        idx = phrase_len
        while idx + phrase_len <= len(lower_words) and lower_words[idx:idx + phrase_len] == phrase:
            repeat_count += 1
            idx += phrase_len
        if repeat_count >= 2:
            return " ".join(words[:phrase_len]).strip()

    for phrase_len in range(3, min(12, len(words) // 2) + 1):
        phrase = lower_words[-phrase_len:]
        idx = len(lower_words) - phrase_len
        repeat_count = 1
        while idx - phrase_len >= 0 and lower_words[idx - phrase_len:idx] == phrase:
            repeat_count += 1
            idx -= phrase_len
        if repeat_count >= 2:
            return " ".join(words[:idx + phrase_len]).strip()

    return normalized


def _trim_pcm16_silence(
    frames: bytes,
    sample_width: int = 2,
    sample_rate: int = 16000,
    threshold: int = 200,
) -> bytes:
    import audioop
    if not frames:
        return frames

    frame_size = max(sample_width, int(sample_rate * 0.01) * sample_width)
    chunks = [frames[i:i + frame_size] for i in range(0, len(frames), frame_size)
              if len(frames[i:i + frame_size]) == frame_size]
    if not chunks:
        return frames

    start = 0
    end = len(chunks)
    while start < end and audioop.rms(chunks[start], sample_width) <= threshold:
        start += 1
    while end > start and audioop.rms(chunks[end - 1], sample_width) <= threshold:
        end -= 1

    return b"".join(chunks[start:end])


def _prepare_wav_for_transcription(wav_path: Path) -> Tuple[bool, str]:
    import audioop
    import wave

    with wave.open(str(wav_path), "rb") as wav_file:
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        sample_rate = wav_file.getframerate()
        frames = wav_file.readframes(wav_file.getnframes())

    trimmed_frames = _trim_pcm16_silence(
        frames,
        sample_width=sample_width,
        sample_rate=sample_rate,
        threshold=MIN_CHUNK_RMS,
    )
    if not trimmed_frames:
        return False, "trimmed to silence"

    rms = audioop.rms(trimmed_frames, sample_width)
    if rms < MIN_CHUNK_RMS:
        return False, f"weak audio rms={rms}"

    frame_size = max(sample_width, int(sample_rate * 0.01) * sample_width)
    sample_count = max(1, len(trimmed_frames) // frame_size)
    active_samples = 0
    for i in range(0, len(trimmed_frames), frame_size):
        frame = trimmed_frames[i:i + frame_size]
        if len(frame) < frame_size:
            continue
        if audioop.rms(frame, sample_width) >= MIN_CHUNK_RMS:
            active_samples += 1
    speech_ratio = active_samples / float(sample_count)
    if speech_ratio < MIN_SPEECH_RATIO:
        return False, f"low speech ratio={speech_ratio:.3f}"

    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(trimmed_frames)

    return True, f"ok rms={rms} speech_ratio={speech_ratio:.3f}"


def _transcribe_chunk_blocking(temp_path: Path, prompt_tail: str) -> Tuple[str, float]:
    model = _load_model()
    whisper_started_at = time.perf_counter()
    segments, _ = model.transcribe(
        str(temp_path),
        language=LANGUAGE,
        condition_on_previous_text=False,
        initial_prompt=prompt_tail or None,
        temperature=(0.0, 0.2, 0.4),
        beam_size=8,
        no_speech_threshold=WHISPER_NO_SPEECH_THRESHOLD,
        vad_filter=True,
        vad_parameters={
            "min_silence_duration_ms": WHISPER_VAD_MIN_SILENCE_MS,
            "speech_pad_ms": WHISPER_VAD_SPEECH_PAD_MS,
            "threshold": WHISPER_VAD_FILTER_THRESHOLD,
        },
    )
    whisper_elapsed_seconds = time.perf_counter() - whisper_started_at
    text = _strip_repetition_noise(
        _strip_repetitive_tail(
            _collapse_repeated_phrases(" ".join(segment.text.strip() for segment in segments if segment.text).strip())
        )
    )
    return text, whisper_elapsed_seconds


def _prompt_tail_for_session(session_id: str) -> str:
    tail = _session_prompt_tail.get(session_id, "")
    if tail:
        return f"{MEDICAL_PROMPT} {tail}".strip()
    return MEDICAL_PROMPT


def _set_prompt_tail_for_session(session_id: str, accumulated_text: str) -> None:
    vocab = _session_medicine_vocab.get(session_id) or set()
    if vocab:
        focused = ", ".join(sorted(vocab)[:30])
        _session_prompt_tail[session_id] = f"Previously mentioned in this session: {focused}."
        return
    normalized = _normalize_text(accumulated_text)
    _session_prompt_tail[session_id] = normalized[-PROMPT_TAIL_CHARS:] if normalized else ""


def _update_session_medicine_vocab(session_id: str, text: str) -> None:
    if not session_id or not text:
        return
    vocab = _session_medicine_vocab.setdefault(session_id, set())
    lower = text.lower()
    for name in COMMON_MEDICINE_TERMS:
        if re.search(rf"\b{re.escape(name.lower())}\b", lower):
            vocab.add(name)
    for name in _extract_medicine_names(text):
        if name:
            vocab.add(name)


def _safe_session_dir(session_id: str) -> Path:
    safe_name = "".join(ch for ch in session_id if ch.isalnum() or ch in ("-", "_")) or "session"
    path = SAVE_AUDIO_DIR / safe_name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_low_value_transcript(text: str) -> bool:
    normalized = _normalize_text(_strip_repetition_noise(text)).lower()
    if not normalized:
        return True
    # Only drop pure hallucination phrases
    low_value_phrases = {
        "thank you", "thank you very much", "bye", "bye bye",
        "you", ".", " ", "thanks",
    }
    if normalized.strip(".,! ") in low_value_phrases:
        return True
    return False

def _is_duplicate_transcript(session_id: str, text: str) -> bool:
    normalized = _normalize_text(text).lower()
    previous = _normalize_text(_last_chunk_text.get(session_id, "")).lower()
    if not normalized or not previous:
        return False
    similarity = SequenceMatcher(None, previous, normalized).ratio()
    return similarity >= DEDUPE_SIMILARITY


def _is_relevant_transcript(text: str) -> bool:
    normalized = _normalize_text(text).lower()
    if not normalized:
        return False
    words = [w.strip(".,!?") for w in normalized.split() if w.strip(".,!?")]
    return len(words) >= MIN_ACCEPTED_WORDS


@app.get("/", response_class=HTMLResponse)
def live_page() -> HTMLResponse:
    return HTMLResponse(
        """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Live Whisper Browser Test</title>
  <style>
    :root {
      --bg: #f4efe7;
      --panel: #fffaf2;
      --ink: #1f2937;
      --muted: #6b7280;
      --accent: #0f766e;
      --accent-2: #b45309;
      --border: #dccfb8;
    }
    body {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      background: linear-gradient(135deg, #efe7db 0%, #f8f4ee 52%, #ece4d8 100%);
      color: var(--ink);
    }
    .wrap {
      max-width: 860px;
      margin: 40px auto;
      padding: 24px;
    }
    .panel {
      background: rgba(255, 250, 242, 0.92);
      border: 1px solid var(--border);
      border-radius: 18px;
      padding: 24px;
      box-shadow: 0 18px 50px rgba(78, 57, 28, 0.08);
      backdrop-filter: blur(10px);
    }
    h1 {
      margin: 0 0 10px;
      font-size: 34px;
    }
    p {
      color: var(--muted);
      line-height: 1.5;
    }
    .row {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      margin: 18px 0;
    }
    button {
      border: 0;
      border-radius: 999px;
      padding: 12px 18px;
      font-size: 15px;
      cursor: pointer;
      color: white;
      background: var(--accent);
    }
    button.secondary {
      background: var(--accent-2);
    }
    button:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
    .meta {
      display: grid;
      gap: 10px;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      margin: 18px 0;
    }
    .card {
      background: #fff;
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 14px;
      min-width: 0;
    }
    .label {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .value {
      margin-top: 6px;
      font-size: 18px;
      word-break: break-word;
    }
    @media (max-width: 720px) {
      .meta {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
    textarea {
      width: 100%;
      min-height: 180px;
      resize: vertical;
      border-radius: 14px;
      border: 1px solid var(--border);
      padding: 16px;
      font-size: 18px;
      line-height: 1.5;
      background: #fff;
      color: var(--ink);
      box-sizing: border-box;
    }
    .grid-panels {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 16px;
      margin-top: 16px;
    }
    @media (max-width: 900px) {
      .grid-panels {
        grid-template-columns: 1fr;
      }
    }
    .section-title {
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin: 8px 0;
      font-size: 14px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 0.06em;
    }
    .toolbar {
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
      margin: 10px 0 0;
    }
    .toolbar button {
      padding: 10px 14px;
      font-size: 14px;
    }
    .toolbar .ghost {
      color: var(--ink);
      background: #efe7db;
    }
    .panel-block {
      background: rgba(255,255,255,0.55);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 12px;
    }
    .latency-chip {
      font-size: 12px;
      color: var(--muted);
    }
    .parchi {
      margin-top: 18px;
      background: #f8f3ea;
      border: 1px solid #c9bfae;
      border-radius: 18px;
      box-shadow: 0 18px 40px rgba(54, 45, 32, 0.1);
      overflow: hidden;
    }
    .parchi-shell {
      position: relative;
      padding: 16px;
    }
    .parchi-template {
      position: absolute;
      inset: 0;
      background-image: url("/parchi-template.png");
      background-size: cover;
      background-position: center top;
      opacity: 0.22;
      pointer-events: none;
    }
    .parchi-overlay {
      position: relative;
      background: rgba(255, 251, 245, 0.9);
      border: 1px solid rgba(185, 173, 151, 0.82);
      border-radius: 16px;
      overflow: hidden;
      backdrop-filter: blur(2px);
    }
    .parchi-head {
      display: grid;
      grid-template-columns: 1.3fr 1fr;
      gap: 12px;
      padding: 14px 18px;
      border-bottom: 1px solid #ccbfa8;
      background: linear-gradient(180deg, rgba(247, 242, 234, 0.96) 0%, rgba(241, 235, 225, 0.94) 100%);
      font-size: 13px;
    }
    .parchi-head strong {
      display: block;
      font-size: 18px;
      margin-bottom: 4px;
    }
    .parchi-body {
      padding: 16px 18px 18px;
      display: grid;
      gap: 12px;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      font-size: 15px;
      line-height: 1.45;
    }
    .parchi-section {
      background: rgba(255, 255, 255, 0.72);
      border: 1px solid #ddd0bc;
      border-radius: 12px;
      padding: 12px 14px;
      min-height: 92px;
    }
    .parchi-section.wide {
      grid-column: 1 / -1;
    }
    .parchi-label {
      font-size: 11px;
      color: #7f715d;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 6px;
    }
    .parchi-value {
      white-space: pre-wrap;
      word-break: break-word;
    }
    .parchi-edit-input,
    .parchi-edit-textarea {
      width: 100%;
      border: 1px solid #b9a893;
      border-radius: 6px;
      padding: 8px;
      font-size: 14px;
      font-family: Georgia, serif;
      background: #fff;
      color: var(--ink);
      box-sizing: border-box;
    }
    .parchi-edit-textarea {
      min-height: 80px;
      resize: vertical;
    }
    .parchi-footer {
      display: flex;
      gap: 8px;
      padding: 12px 18px;
      border-top: 1px solid #ddd0bc;
      justify-content: flex-end;
      background: rgba(255,255,255,0.5);
    }
    .btn-parchi-edit,
    .btn-parchi-save,
    .btn-parchi-cancel {
      border: 0;
      border-radius: 6px;
      padding: 10px 16px;
      font-size: 13px;
      cursor: pointer;
      background: var(--accent);
      color: white;
    }
    .btn-parchi-edit {
      background: var(--accent);
    }
    .btn-parchi-save {
      background: #059669;
    }
    .btn-parchi-cancel {
      background: #b45309;
    }
    .med-row-edit {
      display: grid;
      grid-template-columns: minmax(130px, 1.3fr) minmax(92px, 0.8fr) minmax(130px, 1.1fr) minmax(82px, 0.7fr) minmax(110px, 1fr) 90px;
      gap: 8px;
      padding: 8px 0;
      align-items: center;
      border-bottom: 1px dotted #e2d8c6;
    }
    @media (max-width: 1200px) {
      .med-row-edit {
        grid-template-columns: minmax(120px, 1.1fr) minmax(88px, 0.7fr) minmax(120px, 1fr) minmax(78px, 0.7fr) minmax(100px, 0.9fr) 84px;
      }
    }
    @media (max-width: 900px) {
      .med-row-edit {
        grid-template-columns: 1fr;
        gap: 8px;
      }
    }
    .med-row-edit:last-child {
      border-bottom: 0;
    }
    .med-input,
    .med-select {
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
      border: 1px solid #b9a893;
      border-radius: 4px;
      padding: 6px 8px;
      font-size: 13px;
      background: #fff;
      color: var(--ink);
    }
    .med-delete-btn {
      width: 100%;
      min-width: 0;
      box-sizing: border-box;
      border: 0;
      border-radius: 4px;
      padding: 6px 10px;
      font-size: 12px;
      background: #dc2626;
      color: white;
      cursor: pointer;
    }
    .med-add-btn {
      border: 0;
      border-radius: 4px;
      padding: 8px 12px;
      font-size: 13px;
      background: #0f766e;
      color: white;
      cursor: pointer;
      margin-top: 8px;
    }
    .med-head,
    .med-row {
      display: grid;
      grid-template-columns: 1.3fr 1fr 1fr 1.2fr 1.2fr 80px;
      gap: 8px;
      padding: 6px 0;
      align-items: start;
    }
    @media (max-width: 900px) {
      .parchi-body {
        grid-template-columns: 1fr;
      }
      .med-head,
      .med-row {
        grid-template-columns: 1fr;
      }
    }
    .med-head {
      font-size: 11px;
      color: #7f715d;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      border-bottom: 1px solid #ddd0bc;
      padding-bottom: 8px;
      margin-bottom: 6px;
    }
    .med-row {
      border-bottom: 1px dotted #e2d8c6;
    }
    .med-row:last-child {
      border-bottom: 0;
      padding-bottom: 0;
    }
    .hint {
      font-size: 14px;
      color: var(--muted);
      margin-top: 12px;
    }
    .log {
      margin-top: 14px;
      min-height: 90px;
      border-radius: 14px;
      border: 1px solid var(--border);
      padding: 12px;
      background: #fff;
      font-size: 14px;
      color: var(--ink);
      white-space: pre-wrap;
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>Live Mic to Whisper</h1>
      <p>
        Open this page in your local browser, allow microphone access, then press start.
        Your browser captures your local mic and sends valid WAV chunks to the VM for CPU transcription.
      </p>
      <div class="row">
        <button id="startBtn">Start Mic</button>
        <button id="stopBtn" class="secondary" disabled>Stop</button>
        <button id="clearBtn" type="button">Clear Text</button>
      </div>
      <div class="meta">
        <div class="card">
          <div class="label">Session</div>
          <div class="value" id="sessionValue">Not started</div>
        </div>
        <div class="card">
          <div class="label">Status</div>
          <div class="value" id="statusValue">Idle</div>
        </div>
        <div class="card">
          <div class="label">Last Chunk</div>
          <div class="value" id="chunkValue">0</div>
        </div>
        <div class="card">
          <div class="label">Whisper</div>
          <div class="value" id="whisperCardValue">0.00s</div>
        </div>
        <div class="card">
          <div class="label">Cleaned</div>
          <div class="value" id="cleanCardValue">0.00s</div>
        </div>
      </div>
      <div class="grid-panels">
        <div class="panel-block">
          <div class="section-title">
            <span>Raw Whisper Output</span>
            <span class="latency-chip" id="whisperLatencyValue">Whisper: 0.00s</span>
          </div>
          <textarea id="transcript" placeholder="Live raw transcript will appear here..." readonly></textarea>
        </div>
        <div class="panel-block">
          <div class="section-title">
            <span>Cleaned Output</span>
            <span class="latency-chip" id="cleanLatencyValue">LLM Clean: 0.00s</span>
          </div>
          <textarea id="cleanedTranscript" placeholder="Cleaned output will appear here..." readonly></textarea>
          <div class="toolbar">
            <button id="confirmCleanBtn" type="button">Confirm</button>
            <button id="editCleanBtn" type="button" class="ghost">Edit</button>
            <button id="cancelCleanBtn" type="button" class="ghost">Cancel</button>
          </div>
        </div>
      </div>
      <div class="parchi" id="parchiPreview" hidden>
        <div class="parchi-shell">
          <div class="parchi-template"></div>
          <div class="parchi-overlay">
            <div class="parchi-head">
              <div>
                <strong>Doctor Prescription</strong>
                <div>Click Edit to modify prescription details</div>
              </div>
              <div style="text-align:right">
                <div id="parchiDate"></div>
                <div id="parchiFollowUp"></div>
              </div>
            </div>
            <div class="parchi-body">
              <div class="parchi-section">
                <div class="parchi-label">Patient</div>
                <div id="parchiPatientName">-</div>
              </div>
              <div class="parchi-section">
                <div class="parchi-label">Diagnosis</div>
                <div id="parchiDiagnosis">-</div>
              </div>
              <div class="parchi-section wide">
                <div class="parchi-label">Complaints</div>
                <div id="parchiComplaints">-</div>
              </div>
              <div class="parchi-section wide">
                <div class="parchi-label">Vital Signs</div>
                <div id="parchiVitalSigns" style="display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 12px; margin-top: 8px;">
                  <div><strong>BP:</strong> <span id="parchiBloodPressure">-</span></div>
                  <div><strong>Temp:</strong> <span id="parchiTemperature">-</span></div>
                  <div><strong>Weight:</strong> <span id="parchiWeight">-</span></div>
                </div>
              </div>
              <div class="parchi-section wide">
                <div class="parchi-label">Medicines</div>
                <div id="parchiMedicines">-</div>
              </div>
              <div class="parchi-section">
                <div class="parchi-label">Advice</div>
                <div id="parchiAdvice">-</div>
              </div>
              <div class="parchi-section">
                <div class="parchi-label">Tests</div>
                <div id="parchiTests">-</div>
              </div>
            </div>
            <div class="parchi-footer" style="display: flex; gap: 8px; padding: 12px 18px; border-top: 1px solid #ddd0bc; justify-content: flex-end;">
              <button id="parchiEditBtn" onclick="window.enterParchiEditMode()" style="display: inline-block; border: 0; border-radius: 6px; padding: 10px 16px; font-size: 13px; cursor: pointer; background: var(--accent); color: white;">Edit</button>
              <button id="parchiSaveBtn" onclick="window.saveParchiChanges()" style="display: none; border: 0; border-radius: 6px; padding: 10px 16px; font-size: 13px; cursor: pointer; background: #059669; color: white;">Save</button>
              <button id="parchiCancelBtn" onclick="window.cancelParchiEdit()" style="display: none; border: 0; border-radius: 6px; padding: 10px 16px; font-size: 13px; cursor: pointer; background: #b45309; color: white;">Cancel</button>
            </div>
          </div>
        </div>
      </div>
      <div id="debugLog" class="log">Diagnostics will appear here.</div>
      <div class="hint">
        This is a test-only speech-segmented live transcription page. It waits for short silence before sending a chunk.
      </div>
    </div>
  </div>
  <script>
    const startBtn = document.getElementById("startBtn");
    const stopBtn = document.getElementById("stopBtn");
    const clearBtn = document.getElementById("clearBtn");
    const confirmCleanBtn = document.getElementById("confirmCleanBtn");
    const editCleanBtn = document.getElementById("editCleanBtn");
    const cancelCleanBtn = document.getElementById("cancelCleanBtn");
    const transcriptEl = document.getElementById("transcript");
    const cleanedTranscriptEl = document.getElementById("cleanedTranscript");
    const statusValue = document.getElementById("statusValue");
    const chunkValue = document.getElementById("chunkValue");
    const sessionValue = document.getElementById("sessionValue");
    const whisperCardValue = document.getElementById("whisperCardValue");
    const cleanCardValue = document.getElementById("cleanCardValue");
    const whisperLatencyValue = document.getElementById("whisperLatencyValue");
    const cleanLatencyValue = document.getElementById("cleanLatencyValue");
    const debugLog = document.getElementById("debugLog");
    const parchiPreviewEl = document.getElementById("parchiPreview");

    let stream = null;
    let sessionId = null;
    let chunkCount = 0;
    let isStopping = false;
    const basePath = window.location.pathname.endsWith("/")
      ? window.location.pathname
      : window.location.pathname + "/";
    let audioContext = null;
    let sourceNode = null;
    let processorNode = null;
    let speechChunks = [];
    let preRollChunks = [];
    let candidateSpeechChunks = [];
    let speechSampleCount = 0;
    let silenceSampleCount = 0;
    let candidateSpeechSampleCount = 0;
    let inSpeech = false;
    let isFlushing = false;
    let uploadQueue = [];
    let uploadInFlight = false;
    let isStopped = true;
    let latencySamples = [];
    let cleanRequestSeq = 0;
    let cleanDebounceTimer = null;
    let parchiRenderSeq = 0;
    let currentCaptureMode = "main";
    let activeEditField = "";
    let editFieldSessionId = "";
    let editFieldBaseValue = "";
    let editFieldRequestSeq = 0;

    const CHUNK_SECONDS = __CHUNK_SECONDS__;
    const MAX_SEGMENT_SECONDS = __MAX_SEGMENT_SECONDS__;
    const MIN_SPEECH_SECONDS = __MIN_SPEECH_SECONDS__;
    const SILENCE_SECONDS = __SILENCE_SECONDS__;
    const VAD_THRESHOLD = __VAD_THRESHOLD__;
    const SPEECH_START_SECONDS = __SPEECH_START_SECONDS__;
    const TARGET_SAMPLE_RATE = __TARGET_SAMPLE_RATE__;
    const TRIM_SILENCE_THRESHOLD = __TRIM_SILENCE_THRESHOLD__;
    const MIN_CHUNK_RMS = __MIN_CHUNK_RMS__;

    console.log("[INIT] Parchi system initialized - using inline onclick handlers");

    function setStatus(value) {
      statusValue.textContent = value;
    }

    function generateClientSessionId() {
      return (crypto && crypto.randomUUID) ? crypto.randomUUID() : String(Date.now());
    }

    function isEditCaptureMode() {
      return parchiEditMode && !!activeEditField;
    }

    function getEditFieldLabel(fieldName) {
      const labels = {
        patient_name: "Patient",
        complaints: "Complaints",
        diagnosis: "Diagnosis",
        advice: "Advice",
        tests: "Tests",
        blood_pressure: "BP",
        temperature: "Temperature",
        weight: "Weight",
      };
      if (fieldName.startsWith("medicine_name_")) {
        return "Medicine";
      }
      if (fieldName.startsWith("medicine_frequency_")) {
        return "Frequency";
      }
      if (fieldName.startsWith("medicine_timing_")) {
        return "Timing";
      }
      if (fieldName.startsWith("medicine_duration_")) {
        return "Duration";
      }
      if (fieldName.startsWith("medicine_notes_")) {
        return "Notes";
      }
      return labels[fieldName] || fieldName || "field";
    }

    function getEditFieldElementId(fieldName) {
      const ids = {
        patient_name: "editPatientName",
        complaints: "editComplaints",
        diagnosis: "editDiagnosis",
        advice: "editAdvice",
        tests: "editTests",
        blood_pressure: "editBloodPressure",
        temperature: "editTemperature",
        weight: "editWeight",
      };
      if (fieldName.startsWith("medicine_")) {
        return `editField__${fieldName}`;
      }
      return ids[fieldName] || "";
    }

    function createBlankParchiData() {
      return {
        patient_name: "",
        complaints: "",
        diagnosis: "",
        medicines: [],
        vital_signs: {
          blood_pressure: "",
          temperature: "",
          weight: "",
        },
        advice: "",
        tests: "",
        follow_up: "",
      };
    }

    function resetEditDictationState(clearSelection = true) {
      editFieldSessionId = "";
      editFieldBaseValue = "";
      editFieldRequestSeq = 0;
      if (clearSelection) {
        activeEditField = "";
      }
      document.querySelectorAll("[data-edit-field]").forEach((el) => {
        el.style.outline = "";
        el.style.borderColor = "";
      });
      document.querySelectorAll("[data-view-field]").forEach((el) => {
        el.style.outline = "";
        el.style.borderColor = "";
      });
    }

    function mergeEditFieldValue(fieldName, baseValue, cleanedValue) {
      const base = String(baseValue || "").trim();
      const cleaned = String(cleanedValue || "").trim();
      if (!cleaned) {
        return base;
      }
      if (!base) {
        return cleaned;
      }
      if (
        ["patient_name", "diagnosis", "tests", "blood_pressure", "temperature", "weight"].includes(fieldName)
        || fieldName.startsWith("medicine_")
      ) {
        return cleaned;
      }
      if (base.toLowerCase() === cleaned.toLowerCase() || base.toLowerCase().includes(cleaned.toLowerCase())) {
        return base;
      }
      return `${base} ${cleaned}`.trim();
    }

    function updateActiveEditFieldValue(value) {
      if (!activeEditField) {
        return;
      }
      const elementId = getEditFieldElementId(activeEditField);
      const fieldEl = elementId ? document.getElementById(elementId) : null;
      const merged = mergeEditFieldValue(activeEditField, editFieldBaseValue, value);
      if (fieldEl) {
        fieldEl.value = merged;
      }
      if (activeEditField === "blood_pressure" || activeEditField === "temperature" || activeEditField === "weight") {
        if (!parchiData.vital_signs) {
          parchiData.vital_signs = {};
        }
        parchiData.vital_signs[activeEditField] = merged;
      } else if (activeEditField.startsWith("medicine_")) {
        const match = activeEditField.match(/^medicine_(name|frequency|timing|duration|notes)_(\d+)$/);
        if (match) {
          const medField = match[1];
          const medIndex = Number(match[2]);
          if (!parchiData.medicines) {
            parchiData.medicines = [];
          }
          while (parchiData.medicines.length <= medIndex) {
            parchiData.medicines.push({ name: "", frequency: "", timing: "", duration: "", notes: "" });
          }
          parchiData.medicines[medIndex][medField] = merged;
        }
      } else {
        parchiData[activeEditField] = merged;
      }
    }

    async function cleanActiveEditField(rawText) {
      const text = String(rawText || "").trim();
      if (!text || !activeEditField) {
        return;
      }
      const fieldAtRequest = activeEditField;
      const requestId = ++editFieldRequestSeq;
      const response = await fetch(basePath + "clean-edit-field", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          raw_text: text,
          field_name: fieldAtRequest,
          session_id: editFieldSessionId || "",
        }),
      });
      const payload = await response.json().catch(() => ({ detail: "Invalid edit cleaning response" }));
      if (!response.ok) {
        throw new Error(payload.detail || "Edit cleaning failed");
      }
      if (requestId !== editFieldRequestSeq || fieldAtRequest !== activeEditField) {
        return;
      }
      setCleanLatency(payload.clean_elapsed_seconds || 0);
      updateActiveEditFieldValue(payload.cleaned_value || "");
    }

    function setLatency(seconds) {
      latencySamples.push(seconds);
      return seconds;
    }

    function setWhisperLatency(seconds) {
      const value = Number(seconds || 0).toFixed(2);
      whisperLatencyValue.textContent = `Whisper: ${value}s`;
      whisperCardValue.textContent = `${value}s`;
    }

    function setCleanLatency(seconds) {
      const value = Number(seconds || 0).toFixed(2);
      cleanLatencyValue.textContent = `LLM Clean: ${value}s`;
      cleanCardValue.textContent = `${value}s`;
    }

    function logLine(value) {
      const stamp = new Date().toLocaleTimeString();
      debugLog.textContent = `[${stamp}] ${value}\n` + debugLog.textContent;
    }

    function mergeFloat32Arrays(chunks) {
      let totalLength = 0;
      for (const chunk of chunks) {
        totalLength += chunk.length;
      }
      const merged = new Float32Array(totalLength);
      let offset = 0;
      for (const chunk of chunks) {
        merged.set(chunk, offset);
        offset += chunk.length;
      }
      return merged;
    }

    function downsampleBuffer(samples, inputRate, outputRate) {
      if (outputRate >= inputRate) {
        return samples;
      }
      const ratio = inputRate / outputRate;
      const newLength = Math.max(1, Math.round(samples.length / ratio));
      const result = new Float32Array(newLength);
      let offsetResult = 0;
      let offsetBuffer = 0;

      while (offsetResult < result.length) {
        const nextOffsetBuffer = Math.min(samples.length, Math.round((offsetResult + 1) * ratio));
        let accum = 0;
        let count = 0;
        for (let i = offsetBuffer; i < nextOffsetBuffer; i += 1) {
          accum += samples[i];
          count += 1;
        }
        result[offsetResult] = count ? accum / count : 0;
        offsetResult += 1;
        offsetBuffer = nextOffsetBuffer;
      }
      return result;
    }

    function encodeWav(samples, sampleRate) {
      const buffer = new ArrayBuffer(44 + samples.length * 2);
      const view = new DataView(buffer);

      function writeString(offset, value) {
        for (let i = 0; i < value.length; i += 1) {
          view.setUint8(offset + i, value.charCodeAt(i));
        }
      }

      writeString(0, "RIFF");
      view.setUint32(4, 36 + samples.length * 2, true);
      writeString(8, "WAVE");
      writeString(12, "fmt ");
      view.setUint32(16, 16, true);
      view.setUint16(20, 1, true);
      view.setUint16(22, 1, true);
      view.setUint32(24, sampleRate, true);
      view.setUint32(28, sampleRate * 2, true);
      view.setUint16(32, 2, true);
      view.setUint16(34, 16, true);
      writeString(36, "data");
      view.setUint32(40, samples.length * 2, true);

      let offset = 44;
      for (let i = 0; i < samples.length; i += 1) {
        const sample = Math.max(-1, Math.min(1, samples[i]));
        const int16 = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        view.setInt16(offset, int16, true);
        offset += 2;
      }

      return new Blob([view], { type: "audio/wav" });
    }

    async function sendChunk(blob) {
      const editMode = isEditCaptureMode();
      const targetSessionId = editMode ? (editFieldSessionId || generateClientSessionId()) : sessionId;
      if (!targetSessionId || !blob || blob.size === 0) {
        return;
      }
      if (editMode && !editFieldSessionId) {
        editFieldSessionId = targetSessionId;
      }
      const startedAt = performance.now();
      const formData = new FormData();
      formData.append("session_id", targetSessionId);
      formData.append("audio_chunk", blob, `chunk-${chunkCount}.wav`);
      setStatus("Uploading chunk...");
      const response = await fetch(basePath + "transcribe-chunk", {
        method: "POST",
        body: formData,
      });
      const payload = await response.json().catch(() => ({ detail: "Invalid server response" }));
      if (!response.ok) {
        throw new Error(payload.detail || "Chunk upload failed");
      }
      const latencySeconds = (performance.now() - startedAt) / 1000.0;
      setLatency(latencySeconds);
      setWhisperLatency(payload.whisper_elapsed_seconds || latencySeconds);
      if (editMode) {
        logLine(
          `Edit chunk ${payload.chunk_index} for ${getEditFieldLabel(activeEditField)} processed in ${latencySeconds.toFixed(2)}s. ` +
          `Partial text: ${payload.chunk_text || "(empty)"}`
        );
        if (payload.accumulated_text && payload.chunk_text && activeEditField) {
          cleanActiveEditField(payload.accumulated_text).catch((error) => {
            logLine(`Edit cleaning failed: ${error.message}`);
          });
        }
      } else {
        chunkValue.textContent = String(payload.chunk_index);
        transcriptEl.value = payload.accumulated_text || transcriptEl.value;
        transcriptEl.scrollTop = transcriptEl.scrollHeight;
        logLine(
          `Chunk ${payload.chunk_index} processed in ${latencySeconds.toFixed(2)}s. ` +
          `Partial text: ${payload.chunk_text || "(empty)"}`
        );
        if (payload.accumulated_text && payload.chunk_text) {
          clearTimeout(cleanDebounceTimer);
          cleanDebounceTimer = setTimeout(() => {
            cleanTranscriptFromRaw(payload.accumulated_text).catch((error) => {
              logLine(`Cleaning failed: ${error.message}`);
            });
          }, 1500);
        }
      }
      setStatus(isStopping ? "Finalizing..." : (editMode ? `Listening for ${getEditFieldLabel(activeEditField)}` : "Listening"));
    }

    async function processUploadQueue() {
      if (uploadInFlight) {
        return;
      }
      uploadInFlight = true;
      try {
        while (uploadQueue.length > 0) {
          const nextBlob = uploadQueue.shift();
          try {
            await sendChunk(nextBlob);
          } catch (error) {
            logLine(`Chunk upload failed: ${error.message}`);
            setStatus("Error: " + error.message);
          }
        }
      } finally {
        uploadInFlight = false;
      }
    }

    function computeRms(samples) {
      let sum = 0;
      for (let i = 0; i < samples.length; i += 1) {
        sum += samples[i] * samples[i];
      }
      return Math.sqrt(sum / Math.max(1, samples.length));
    }

    function trimTrailingSilence(samples, threshold) {
      let end = samples.length;
      while (end > 0 && Math.abs(samples[end - 1]) < threshold) {
        end -= 1;
      }
      let start = 0;
      while (start < end && Math.abs(samples[start]) < threshold) {
        start += 1;
      }
      return samples.slice(start, end);
    }

    function resetSegmentState() {
      speechChunks = [];
      candidateSpeechChunks = [];
      speechSampleCount = 0;
      silenceSampleCount = 0;
      candidateSpeechSampleCount = 0;
      inSpeech = false;
    }

    function escapeHtml(value) {
      return String(value || "")
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#39;");
    }

    function renderMedicines(medicines) {
      if (!Array.isArray(medicines) || medicines.length === 0) {
        return "-";
      }
      return `
        <div class="med-head">
          <div>Medicine</div>
          <div>Frequency</div>
          <div>Timing</div>
          <div>Duration</div>
          <div>Notes</div>
          <div>Action</div>
        </div>
      ` + medicines.map((item, idx) => `
        <div class="med-row">
          <div><strong>${escapeHtml(item.name || "-")}</strong></div>
          <div>${escapeHtml(item.frequency || "-")}</div>
          <div>${escapeHtml(item.timing || "-")}</div>
          <div>${escapeHtml(item.duration || "-")}</div>
          <div>${escapeHtml(item.notes || "-")}</div>
          <div><button class="med-delete-btn" onclick="deleteMedicine(${idx})">Delete</button></div>
        </div>
      `).join("");
    }

    let parchiEditMode = false;
    let parchiData = {};

    function renderMedicinesEditable(medicines) {
      if (!Array.isArray(medicines)) {
        medicines = [];
      }
      const rows = medicines.map((item, idx) => `
        <div class="med-row-edit" data-med-idx="${idx}">
          <input type="text" class="med-input" placeholder="Medicine name" value="${escapeHtml(item.name || "")}" data-field="name" data-edit-field="medicine_name_${idx}" id="editField__medicine_name_${idx}" />
          <select class="med-select" data-field="frequency" data-edit-field="medicine_frequency_${idx}" id="editField__medicine_frequency_${idx}" title="Select frequency">
            <option value="">Frequency</option>
            <option value="OD" ${item.frequency === "OD" ? "selected" : ""}>OD</option>
            <option value="BD" ${item.frequency === "BD" ? "selected" : ""}>BD</option>
            <option value="TDS" ${item.frequency === "TDS" ? "selected" : ""}>TDS</option>
            <option value="QID" ${item.frequency === "QID" ? "selected" : ""}>QID</option>
          </select>
          <select class="med-select" data-field="timing" data-edit-field="medicine_timing_${idx}" id="editField__medicine_timing_${idx}" title="Select timing">
            <option value="">Timing</option>
            <option value="Morning" ${item.timing === "Morning" ? "selected" : ""}>Morning</option>
            <option value="Afternoon" ${item.timing === "Afternoon" ? "selected" : ""}>Afternoon</option>
            <option value="Evening" ${item.timing === "Evening" ? "selected" : ""}>Evening</option>
            <option value="Night" ${item.timing === "Night" ? "selected" : ""}>Night</option>
            <option value="Morning and Afternoon" ${item.timing === "Morning and Afternoon" ? "selected" : ""}>Morning and Afternoon</option>
            <option value="Morning and Evening" ${item.timing === "Morning and Evening" ? "selected" : ""}>Morning and Evening</option>
            <option value="Morning and Night" ${item.timing === "Morning and Night" ? "selected" : ""}>Morning and Night</option>
            <option value="Afternoon and Evening" ${item.timing === "Afternoon and Evening" ? "selected" : ""}>Afternoon and Evening</option>
            <option value="Afternoon and Night" ${item.timing === "Afternoon and Night" ? "selected" : ""}>Afternoon and Night</option>
            <option value="Evening and Night" ${item.timing === "Evening and Night" ? "selected" : ""}>Evening and Night</option>
            <option value="Morning Afternoon and Evening" ${item.timing === "Morning Afternoon and Evening" ? "selected" : ""}>Morning Afternoon and Evening</option>
            <option value="Morning Afternoon and Night" ${item.timing === "Morning Afternoon and Night" ? "selected" : ""}>Morning Afternoon and Night</option>
            <option value="Morning Evening and Night" ${item.timing === "Morning Evening and Night" ? "selected" : ""}>Morning Evening and Night</option>
            <option value="Afternoon Evening and Night" ${item.timing === "Afternoon Evening and Night" ? "selected" : ""}>Afternoon Evening and Night</option>
            <option value="Morning Afternoon Evening and Night" ${item.timing === "Morning Afternoon Evening and Night" ? "selected" : ""}>Morning Afternoon Evening and Night</option>
          </select>
          <input type="text" class="med-input" placeholder="Duration" value="${escapeHtml(item.duration || "")}" data-field="duration" data-edit-field="medicine_duration_${idx}" id="editField__medicine_duration_${idx}" />
          <select class="med-select" data-field="notes" data-edit-field="medicine_notes_${idx}" id="editField__medicine_notes_${idx}" title="Select notes">
            <option value="">Notes</option>
            <option value="Before food" ${item.notes === "Before food" ? "selected" : ""}>Before food</option>
            <option value="After food" ${item.notes === "After food" ? "selected" : ""}>After food</option>
            <option value="Empty stomach" ${item.notes === "Empty stomach" ? "selected" : ""}>Empty stomach</option>
          </select>
          <button class="med-delete-btn" onclick="deleteMedicine(${idx})">Delete</button>
        </div>
      `).join("");
      return rows + `
        <button class="med-add-btn" onclick="addMedicineRow()">+ Add Medicine</button>
      `;
    }

    window.deleteMedicine = function (idx) {
      if (parchiData.medicines && Array.isArray(parchiData.medicines)) {
        parchiData.medicines.splice(idx, 1);
        updateMedicinesDisplay();
      }
    };

    window.addMedicineRow = function () {
      if (!parchiData.medicines) {
        parchiData.medicines = [];
      }
      parchiData.medicines.push({
        name: "",
        frequency: "",
        timing: "",
        duration: "",
        notes: ""
      });
      updateMedicinesDisplay();
    };

    function updateMedicinesDisplay() {
      const container = document.getElementById("parchiMedicines");
      container.innerHTML = renderMedicinesEditable(parchiData.medicines || []);
    }

    function collectEditedMedicines() {
      const rows = document.querySelectorAll(".med-row-edit");
      const medicines = [];
      rows.forEach(row => {
        const inputs = row.querySelectorAll("input, select");
        const med = {};
        inputs.forEach(inp => {
          med[inp.dataset.field] = inp.value;
        });
        if (med.name || med.frequency || med.duration) {
          medicines.push(med);
        }
      });
      return medicines;
    }

    async function activateEditField(fieldName) {
      if (!parchiEditMode) {
        parchiEditMode = true;
        updateParchiDisplay();
      }
      const elementId = getEditFieldElementId(fieldName);
      const fieldEl = elementId ? document.getElementById(elementId) : null;
      if (!fieldEl) {
        return;
      }
      if (!isStopped && activeEditField && activeEditField !== fieldName) {
        resetSegmentState();
        uploadQueue = [];
        uploadInFlight = false;
        isFlushing = false;
      }
      resetEditDictationState(false);
      activeEditField = fieldName;
      editFieldSessionId = generateClientSessionId();
      editFieldBaseValue = String(fieldEl.value || "").trim();
      editFieldRequestSeq += 1;
      fieldEl.style.outline = "2px solid #0f766e";
      fieldEl.style.borderColor = "#0f766e";
      logLine(`Edit dictation armed for ${getEditFieldLabel(fieldName)}.`);
      if (isStopped) {
        await startCapture("edit");
      } else {
        currentCaptureMode = "edit";
        setStatus(`Listening for ${getEditFieldLabel(fieldName)}`);
      }
    }

    function attachPreviewFieldHandlers() {
      document.querySelectorAll("[data-view-field]").forEach((el) => {
        const fieldName = el.dataset.viewField || "";
        if (!fieldName) {
          return;
        }
        const handler = () => {
          activateEditField(fieldName).catch((error) => {
            logLine(`Edit dictation failed: ${error.message}`);
            setStatus("Error: " + error.message);
          });
        };
        el.addEventListener("click", handler);
      });
    }

    function attachEditFieldDictationHandlers() {
      document.querySelectorAll("[data-edit-field]").forEach((el) => {
        const fieldName = el.dataset.editField || "";
        if (!fieldName) {
          return;
        }
        const handler = () => {
          activateEditField(fieldName).catch((error) => {
            logLine(`Edit dictation failed: ${error.message}`);
            setStatus("Error: " + error.message);
          });
        };
        el.addEventListener("focus", handler);
        el.addEventListener("click", handler);
      });
    }

    function bindDelegatedFieldSelection() {
      const routeFieldSelection = (target) => {
        const editTarget = target?.closest?.("[data-edit-field]");
        if (editTarget) {
          const fieldName = editTarget.dataset.editField || "";
          if (!fieldName) {
            return;
          }
          activateEditField(fieldName).catch((error) => {
            logLine(`Edit dictation failed: ${error.message}`);
            setStatus("Error: " + error.message);
          });
          return;
        }

        const viewTarget = target?.closest?.("[data-view-field]");
        if (viewTarget) {
          const fieldName = viewTarget.dataset.viewField || "";
          if (!fieldName) {
            return;
          }
          activateEditField(fieldName).catch((error) => {
            logLine(`Edit dictation failed: ${error.message}`);
            setStatus("Error: " + error.message);
          });
        }
      };

      document.addEventListener("click", (event) => {
        routeFieldSelection(event.target);
      });

      document.addEventListener("focusin", (event) => {
        routeFieldSelection(event.target);
      });
    }

    function renderParchi(payload) {
      console.log("[PARCHI] Rendering with payload:", payload);
      parchiData = JSON.parse(JSON.stringify(payload));
      parchiEditMode = false;
      resetEditDictationState(true);
      updateParchiDisplay();
      parchiPreviewEl.hidden = false;
      console.log("[PARCHI] Preview visible, Edit button accessible");
    }

    function updateParchiDisplay() {
      document.getElementById("parchiDate").textContent = `Date: ${new Date().toLocaleDateString()}`;
      document.getElementById("parchiFollowUp").textContent = parchiData.follow_up ? `Follow-up: ${parchiData.follow_up}` : "";
      
      // Update vital signs with null checks
      const bpEl = document.getElementById("parchiBloodPressure");
      const tempEl = document.getElementById("parchiTemperature");
      const wtEl = document.getElementById("parchiWeight");
      
      const vs = parchiData.vital_signs || {};
      if (bpEl) bpEl.textContent = escapeHtml(vs.blood_pressure || "-");
      if (tempEl) tempEl.textContent = escapeHtml(vs.temperature || "-");
      if (wtEl) wtEl.textContent = escapeHtml(vs.weight || "-");
      
      if (parchiEditMode) {
        // Edit mode
        document.getElementById("parchiPatientName").innerHTML = `<input type="text" class="parchi-edit-input" id="editPatientName" data-edit-field="patient_name" value="${escapeHtml(parchiData.patient_name || "")}" />`;
        document.getElementById("parchiComplaints").innerHTML = `<textarea class="parchi-edit-textarea" id="editComplaints" data-edit-field="complaints">${escapeHtml(parchiData.complaints || "")}</textarea>`;
        document.getElementById("parchiDiagnosis").innerHTML = `<textarea class="parchi-edit-textarea" id="editDiagnosis" data-edit-field="diagnosis">${escapeHtml(parchiData.diagnosis || "")}</textarea>`;
        document.getElementById("parchiAdvice").innerHTML = `<textarea class="parchi-edit-textarea" id="editAdvice" data-edit-field="advice">${escapeHtml(parchiData.advice || "")}</textarea>`;
        document.getElementById("parchiTests").innerHTML = `<input type="text" class="parchi-edit-input" id="editTests" data-edit-field="tests" value="${escapeHtml(parchiData.tests || "")}" />`;
        document.getElementById("parchiMedicines").innerHTML = renderMedicinesEditable(parchiData.medicines || []);
        
        // Add vital signs edit fields
        const vsEditEl = document.getElementById("parchiVitalSigns");
        if (vsEditEl) {
          vsEditEl.innerHTML = `
            <div><strong>BP:</strong> <input type="text" class="parchi-edit-input" id="editBloodPressure" data-edit-field="blood_pressure" value="${escapeHtml(vs.blood_pressure || "")}" placeholder="e.g., 120/80 mmHg" /></div>
            <div><strong>Temp:</strong> <input type="text" class="parchi-edit-input" id="editTemperature" data-edit-field="temperature" value="${escapeHtml(vs.temperature || "")}" placeholder="e.g., 98.6°F" /></div>
            <div><strong>Weight:</strong> <input type="text" class="parchi-edit-input" id="editWeight" data-edit-field="weight" value="${escapeHtml(vs.weight || "")}" placeholder="e.g., 70 kg" /></div>
          `;
        }
        attachEditFieldDictationHandlers();
        
        document.getElementById("parchiEditBtn").style.display = "none";
        document.getElementById("parchiSaveBtn").style.display = "inline-block";
        document.getElementById("parchiCancelBtn").style.display = "inline-block";
      } else {
        // Preview mode
        document.getElementById("parchiPatientName").innerHTML = `<div class="parchi-value" data-view-field="patient_name">${escapeHtml(parchiData.patient_name || "-")}</div>`;
        document.getElementById("parchiComplaints").innerHTML = `<div class="parchi-value" data-view-field="complaints">${escapeHtml(parchiData.complaints || "-")}</div>`;
        document.getElementById("parchiDiagnosis").innerHTML = `<div class="parchi-value" data-view-field="diagnosis">${escapeHtml(parchiData.diagnosis || "-")}</div>`;
        document.getElementById("parchiAdvice").innerHTML = `<div class="parchi-value" data-view-field="advice">${escapeHtml(parchiData.advice || "-")}</div>`;
        document.getElementById("parchiTests").innerHTML = `<div class="parchi-value" data-view-field="tests">${escapeHtml(parchiData.tests || "-")}</div>`;
        document.getElementById("parchiMedicines").innerHTML = renderMedicines(parchiData.medicines || []);
        
        // Vital signs display mode (already updated above)
        const vsDisplayEl = document.getElementById("parchiVitalSigns");
        if (vsDisplayEl) {
          vsDisplayEl.innerHTML = `
            <div data-view-field="blood_pressure"><strong>BP:</strong> <span id="parchiBloodPressure">-</span></div>
            <div data-view-field="temperature"><strong>Temp:</strong> <span id="parchiTemperature">-</span></div>
            <div data-view-field="weight"><strong>Weight:</strong> <span id="parchiWeight">-</span></div>
          `;
          // Update after rendering
          const bpSpan = document.getElementById("parchiBloodPressure");
          const tempSpan = document.getElementById("parchiTemperature");
          const wtSpan = document.getElementById("parchiWeight");
          if (bpSpan) bpSpan.textContent = escapeHtml(vs.blood_pressure || "-");
          if (tempSpan) tempSpan.textContent = escapeHtml(vs.temperature || "-");
          if (wtSpan) wtSpan.textContent = escapeHtml(vs.weight || "-");
        }
        
        document.getElementById("parchiEditBtn").style.display = "inline-block";
        document.getElementById("parchiSaveBtn").style.display = "none";
        document.getElementById("parchiCancelBtn").style.display = "none";
        attachPreviewFieldHandlers();
      }
    }

    window.enterParchiEditMode = function () {
      parchiEditMode = true;
      resetEditDictationState(true);
      updateParchiDisplay();
      setStatus("Edit ready");
    };

    window.saveParchiChanges = function () {
      try {
        stopCapture();
        console.log("[PARCHI_EDIT] Saving changes...");
        parchiData.patient_name = document.getElementById("editPatientName").value || "";
        parchiData.complaints = document.getElementById("editComplaints").value || "";
        parchiData.diagnosis = document.getElementById("editDiagnosis").value || "";
        parchiData.advice = document.getElementById("editAdvice").value || "";
        parchiData.tests = document.getElementById("editTests").value || "";
        parchiData.medicines = collectEditedMedicines();
        
        // Save vital signs
        const bpEl = document.getElementById("editBloodPressure");
        const tempEl = document.getElementById("editTemperature");
        const wtEl = document.getElementById("editWeight");
        
        parchiData.vital_signs = {
          blood_pressure: (bpEl?.value || "").trim(),
          temperature: (tempEl?.value || "").trim(),
          weight: (wtEl?.value || "").trim()
        };
        
        console.log("[PARCHI_EDIT] Data collected:", parchiData);
        parchiEditMode = false;
        resetEditDictationState(true);
        updateParchiDisplay();
        logLine("✓ Prescription saved successfully.");
      } catch (error) {
        console.error("[PARCHI_EDIT] Error saving:", error);
        logLine("✗ Error saving prescription: " + error.message);
      }
    };

    window.cancelParchiEdit = function () {
      stopCapture();
      parchiEditMode = false;
      resetEditDictationState(true);
      updateParchiDisplay();
      logLine("Prescription edit cancelled.");
    };

    async function cleanTranscriptFromRaw(rawText) {
      const text = String(rawText || "").trim();
      if (!text) {
        cleanedTranscriptEl.value = "";
        parchiPreviewEl.hidden = true;
        setCleanLatency(0);
        return;
      }
      const requestId = ++cleanRequestSeq;
      const response = await fetch(basePath + "clean-transcript", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ raw_text: text, session_id: sessionId || "" }),
      });
      const payload = await response.json().catch(() => ({ detail: "Invalid cleaning response" }));
      if (!response.ok) {
        throw new Error(payload.detail || "Cleaning failed");
      }
      if (requestId !== cleanRequestSeq) {
        return;
      }
      cleanedTranscriptEl.value = payload.cleaned_text || "";
      cleanedTranscriptEl.readOnly = true;
      setCleanLatency(payload.clean_elapsed_seconds || 0);
      
      // Auto-render Parchi after cleaning is done
      try {
        logLine("Cleaning complete. Rendering prescription...");
        await confirmParchi();
      } catch (error) {
        console.error("[PARCHI] Auto-render failed:", error);
        logLine(`Auto-render failed: ${error.message}. Click Confirm to retry.`);
      }
    }

    async function confirmParchi() {
      const text = String(cleanedTranscriptEl.value || "").trim();
      if (!text && !sessionId) {
        return;
      }
      const requestId = ++parchiRenderSeq;
      const response = await fetch(basePath + "render-parchi", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({ cleaned_text: text, session_id: sessionId || "" }),
      });
      const payload = await response.json().catch(() => ({ detail: "Invalid parchi response" }));
      if (!response.ok) {
        throw new Error(payload.detail || "Parchi render failed");
      }
      if (requestId !== parchiRenderSeq) {
        return;
      }
      renderParchi(payload.parchi || {});
      logLine(`✓ Prescription rendered in ${Number(payload.parchi_elapsed_seconds || 0).toFixed(2)}s.`);
      
      // Hide the Confirm/Edit/Cancel buttons after successful rendering
      confirmCleanBtn.style.display = "none";
      editCleanBtn.style.display = "none";
      cancelCleanBtn.style.display = "none";
    }

    async function flushSpeechBuffer(force = false) {
      if (!audioContext || isFlushing) {
        return;
      }
      const minSamples = Math.floor(audioContext.sampleRate * MIN_SPEECH_SECONDS);
      if (!speechSampleCount || (!force && speechSampleCount < minSamples)) {
        return;
      }
      isFlushing = true;
      const merged = mergeFloat32Arrays(speechChunks);
      resetSegmentState();
      chunkCount += 1;
      const downsampled = downsampleBuffer(merged, audioContext.sampleRate, TARGET_SAMPLE_RATE);
      const trimmed = trimTrailingSilence(downsampled, TRIM_SILENCE_THRESHOLD);
      const rms = computeRms(trimmed);
      if (!trimmed.length || rms * 32768 < MIN_CHUNK_RMS) {
        logLine(`Skipped weak chunk ${chunkCount} (samples=${trimmed.length}, rms=${(rms * 32768).toFixed(1)}).`);
        isFlushing = false;
        return;
      }
      const wavBlob = encodeWav(trimmed, TARGET_SAMPLE_RATE);
      logLine(`Captured chunk ${chunkCount} (${wavBlob.size} bytes WAV).`);
      uploadQueue.push(wavBlob);
      processUploadQueue().catch((error) => {
        logLine(`Upload queue failed: ${error.message}`);
        setStatus("Error: " + error.message);
      });
      isFlushing = false;
    }

    async function startCapture(mode = "main") {
      try {
        currentCaptureMode = mode;
        if (mode === "main") {
          sessionId = generateClientSessionId();
          chunkCount = 0;
          sessionValue.textContent = sessionId;
          transcriptEl.value = "";
          cleanedTranscriptEl.value = "";
          if (!parchiData || typeof parchiData !== "object" || Array.isArray(parchiData)) {
            parchiData = createBlankParchiData();
          }
          updateParchiDisplay();
          parchiPreviewEl.hidden = false;
          debugLog.textContent = "";
          whisperCardValue.textContent = "0.00s";
          cleanCardValue.textContent = "0.00s";
          resetEditDictationState(true);
        } else {
          if (!editFieldSessionId) {
            editFieldSessionId = generateClientSessionId();
          }
          if (!activeEditField) {
            throw new Error("Select a prescription field before speaking.");
          }
        }
        isStopping = false;
        isStopped = false;
        isFlushing = false;
        uploadQueue = [];
        uploadInFlight = false;
        latencySamples = [];

        if (!window.isSecureContext) {
          logLine("Browser is not in a secure context. Microphone access usually requires HTTPS or localhost.");
        }
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
          throw new Error("This browser does not expose mediaDevices.getUserMedia.");
        }
        if (typeof MediaRecorder === "undefined") {
          logLine("MediaRecorder not available. Using Web Audio PCM capture instead.");
        }

        setStatus(mode === "edit" ? `Requesting mic for ${getEditFieldLabel(activeEditField)}...` : "Requesting mic...");
        logLine("Requesting microphone permission from the browser.");

        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            channelCount: 1,
            noiseSuppression: true,
            echoCancellation: true,
            autoGainControl: true,
          }
        });
        logLine("Microphone access granted.");
        const AudioContextClass = window.AudioContext || window.webkitAudioContext;
        if (!AudioContextClass) {
          throw new Error("This browser does not support AudioContext.");
        }

        audioContext = new AudioContextClass();
        sourceNode = audioContext.createMediaStreamSource(stream);
        processorNode = audioContext.createScriptProcessor(4096, 1, 1);

        processorNode.onaudioprocess = (event) => {
          if (isStopped) {
            return;
          }
          const input = event.inputBuffer.getChannelData(0);
          const samples = new Float32Array(input);
          const rms = computeRms(samples);
          const isSpeechLike = rms >= VAD_THRESHOLD;

          preRollChunks.push(samples);
          if (preRollChunks.length > 6) {
            preRollChunks.shift();
          }

          if (!inSpeech && isSpeechLike) {
            candidateSpeechChunks.push(samples);
            candidateSpeechSampleCount += samples.length;
            if (candidateSpeechSampleCount < audioContext.sampleRate * SPEECH_START_SECONDS) {
              return;
            }
            inSpeech = true;
            speechChunks = preRollChunks.concat(candidateSpeechChunks);
            speechSampleCount = speechChunks.reduce((sum, chunk) => sum + chunk.length, 0);
            silenceSampleCount = 0;
            candidateSpeechChunks = [];
            candidateSpeechSampleCount = 0;
            logLine(`Speech started (rms=${rms.toFixed(4)}).`);
            return;
          }

          if (!inSpeech) {
            if (!isSpeechLike) {
              candidateSpeechChunks = [];
              candidateSpeechSampleCount = 0;
            }
            return;
          }

          speechChunks.push(samples);
          speechSampleCount += samples.length;

          if (isSpeechLike) {
            silenceSampleCount = 0;
          } else {
            silenceSampleCount += samples.length;
          }

          const segmentSeconds = speechSampleCount / audioContext.sampleRate;
          const silenceSeconds = silenceSampleCount / audioContext.sampleRate;

          if (segmentSeconds >= MAX_SEGMENT_SECONDS) {
            logLine(`Segment reached max length ${segmentSeconds.toFixed(2)}s. Sending chunk.`);
            flushSpeechBuffer(true).catch((error) => {
              logLine(`Flush failed: ${error.message}`);
              setStatus("Error: " + error.message);
            });
            return;
          }

          if (silenceSeconds >= SILENCE_SECONDS && segmentSeconds >= MIN_SPEECH_SECONDS) {
            logLine(`Detected ${silenceSeconds.toFixed(2)}s silence after ${segmentSeconds.toFixed(2)}s speech.`);
            flushSpeechBuffer(true).catch((error) => {
              logLine(`Flush failed: ${error.message}`);
              setStatus("Error: " + error.message);
            });
          }
        };

        sourceNode.connect(processorNode);
        processorNode.connect(audioContext.destination);
        logLine(
          `PCM capture started at ${audioContext.sampleRate} Hz. ` +
          `Target WAV ${TARGET_SAMPLE_RATE} Hz, VAD threshold ${VAD_THRESHOLD}, silence ${SILENCE_SECONDS}s, max segment ${MAX_SEGMENT_SECONDS}s.`
        );
        setStatus(mode === "edit" ? `Listening for ${getEditFieldLabel(activeEditField)}` : "Listening");
        startBtn.disabled = true;
        stopBtn.disabled = false;
      } catch (error) {
        logLine(`Start failed: ${error.message}`);
        setStatus("Error: " + error.message);
        startBtn.disabled = false;
        stopBtn.disabled = true;
      }
    }

    function stopCapture() {
      if (isStopped) {
        return Promise.resolve();
      }
      isStopping = true;
      isStopped = true;
      clearTimeout(cleanDebounceTimer);
      setStatus("Stopping...");
      if (processorNode) {
        processorNode.onaudioprocess = null;
      }
      return Promise.resolve()
        .then(() => flushSpeechBuffer(true))
        .finally(() => {
          if (processorNode) {
            processorNode.disconnect();
            processorNode.onaudioprocess = null;
            processorNode = null;
          }
          if (sourceNode) {
            sourceNode.disconnect();
            sourceNode = null;
          }
          if (audioContext) {
            audioContext.close();
            audioContext = null;
          }
          if (stream) {
            stream.getTracks().forEach((track) => track.stop());
            stream = null;
          }
          preRollChunks = [];
          resetSegmentState();
          logLine("PCM capture stopped.");
          setStatus(parchiEditMode ? "Edit ready" : "Stopped");
          startBtn.disabled = false;
          stopBtn.disabled = true;
        });
    }

    startBtn.addEventListener("click", () => {
      const mode = isEditCaptureMode() ? "edit" : "main";
      startCapture(mode).catch((error) => {
        logLine(`Start failed: ${error.message}`);
        setStatus("Error: " + error.message);
      });
    });

    stopBtn.addEventListener("click", () => {
      stopCapture().catch((error) => {
        logLine(`Stop failed: ${error.message}`);
        setStatus("Error: " + error.message);
      });
    });

    clearBtn.addEventListener("click", () => {
      stopCapture();
      clearTimeout(cleanDebounceTimer);
      transcriptEl.value = "";
      cleanedTranscriptEl.value = "";
      transcriptEl.readOnly = true;
      cleanedTranscriptEl.readOnly = true;
      setStatus("Idle");
      chunkValue.textContent = "0";
      sessionValue.textContent = "Not started";
      whisperCardValue.textContent = "0.00s";
      cleanCardValue.textContent = "0.00s";
      whisperLatencyValue.textContent = "Whisper: 0.00s";
      cleanLatencyValue.textContent = "LLM Clean: 0.00s";
      debugLog.textContent = "Diagnostics will appear here.";
      preRollChunks = [];
      uploadQueue = [];
      uploadInFlight = false;
      cleanRequestSeq = 0;
      parchiData = createBlankParchiData();
      updateParchiDisplay();
      parchiPreviewEl.hidden = false;
      resetEditDictationState(true);
      resetSegmentState();
    });

    editCleanBtn.addEventListener("click", () => {
      cleanedTranscriptEl.readOnly = !cleanedTranscriptEl.readOnly;
    });

    cancelCleanBtn.addEventListener("click", () => {
      cleanedTranscriptEl.value = "";
      cleanedTranscriptEl.readOnly = true;
      if (!parchiData || typeof parchiData !== "object" || Array.isArray(parchiData)) {
        parchiData = createBlankParchiData();
      }
      updateParchiDisplay();
      parchiPreviewEl.hidden = false;
      setCleanLatency(0);
    });

    confirmCleanBtn.addEventListener("click", () => {
      confirmParchi().catch((error) => {
        logLine(`Parchi render failed: ${error.message}`);
        setStatus("Error: " + error.message);
      });
    });

    bindDelegatedFieldSelection();
    parchiData = createBlankParchiData();
    updateParchiDisplay();
    parchiPreviewEl.hidden = false;
    logLine(`Page loaded. Secure context: ${window.isSecureContext ? "yes" : "no"}`);
  </script>
</body>
</html>
        """
        .replace("__CHUNK_SECONDS__", str(CHUNK_SECONDS))
        .replace("__MAX_SEGMENT_SECONDS__", str(MAX_SEGMENT_SECONDS))
        .replace("__MIN_SPEECH_SECONDS__", str(MIN_SPEECH_SECONDS))
        .replace("__SILENCE_SECONDS__", str(SILENCE_SECONDS))
        .replace("__VAD_THRESHOLD__", str(VAD_THRESHOLD))
        .replace("__SPEECH_START_SECONDS__", str(SPEECH_START_SECONDS))
        .replace("__TARGET_SAMPLE_RATE__", str(TARGET_SAMPLE_RATE))
        .replace("__TRIM_SILENCE_THRESHOLD__", str(TRIM_SILENCE_THRESHOLD))
        .replace("__MIN_CHUNK_RMS__", str(MIN_CHUNK_RMS))
        .replace("__CHUNK_MS__", str(CHUNK_MS))
    )


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


@app.get("/parchi-template.png")
def parchi_template() -> FileResponse:
    return FileResponse(Path("Doctor_parchi.png").resolve())


@app.post("/transcribe-chunk")
async def transcribe_chunk(request: Request):
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse({"detail": f"Unable to parse multipart form: {exc}"}, status_code=400)

    session_id = str(form.get("session_id") or "").strip()
    audio_chunk = form.get("audio_chunk")
    if not session_id:
        return JSONResponse({"detail": "Missing session_id"}, status_code=400)
    if audio_chunk is None:
        return JSONResponse({"detail": "Missing audio_chunk file"}, status_code=400)

    filename = getattr(audio_chunk, "filename", None) or "chunk.webm"
    suffix = Path(filename).suffix or ".webm"

    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(await audio_chunk.read())

    ok_for_transcription, reason = _prepare_wav_for_transcription(temp_path)
    if not ok_for_transcription:
        if temp_path.exists():
            temp_path.unlink()
        return JSONResponse(
            {
                "session_id": session_id,
                "chunk_index": _chunk_counts.get(session_id, 0),
                "chunk_text": "",
                "accumulated_text": _normalize_text(" ".join(_session_store.get(session_id, []))),
                "saved_audio_path": "",
                "skipped": True,
                "skip_reason": reason,
            }
        )
    accepted_audio_bytes = temp_path.read_bytes()

    try:
        prompt_tail = _prompt_tail_for_session(session_id)
        text, whisper_elapsed_seconds = await asyncio.wait_for(
            run_in_threadpool(_transcribe_chunk_blocking, temp_path, prompt_tail),
            timeout=WHISPER_REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"detail": f"Whisper transcription timed out after {WHISPER_REQUEST_TIMEOUT_SECONDS:.1f}s"},
            status_code=504,
        )
    except Exception as exc:
        return JSONResponse({"detail": f"Whisper transcription failed: {exc}"}, status_code=500)
    finally:
        if temp_path.exists():
            temp_path.unlink()

    if _is_low_value_transcript(text):
        return JSONResponse(
            {
                "session_id": session_id,
                "chunk_index": _chunk_counts.get(session_id, 0),
                "chunk_text": "",
                "accumulated_text": _normalize_text(" ".join(_session_store.get(session_id, []))),
                "saved_audio_path": "",
                "skipped": True,
                "skip_reason": "low-value transcript",
                "whisper_elapsed_seconds": whisper_elapsed_seconds,
            }
        )
    if not _is_relevant_transcript(text):
        return JSONResponse(
            {
                "session_id": session_id,
                "chunk_index": _chunk_counts.get(session_id, 0),
                "chunk_text": "",
                "accumulated_text": _normalize_text(" ".join(_session_store.get(session_id, []))),
                "saved_audio_path": "",
                "skipped": True,
                "skip_reason": "irrelevant transcript",
                "whisper_elapsed_seconds": whisper_elapsed_seconds,
            }
        )
    if _is_duplicate_transcript(session_id, text):
        return JSONResponse(
            {
                "session_id": session_id,
                "chunk_index": _chunk_counts.get(session_id, 0),
                "chunk_text": "",
                "accumulated_text": _normalize_text(" ".join(_session_store.get(session_id, []))),
                "saved_audio_path": "",
                "skipped": True,
                "skip_reason": "duplicate transcript",
                "whisper_elapsed_seconds": whisper_elapsed_seconds,
            }
        )

    if session_id not in _chunk_counts:
        _chunk_counts[session_id] = 0
    _chunk_counts[session_id] += 1
    chunk_index = _chunk_counts[session_id]
    saved_path = _safe_session_dir(session_id) / f"chunk-{chunk_index:04d}{suffix}"
    saved_path.write_bytes(accepted_audio_bytes)
    if session_id not in _session_store:
        _session_store[session_id] = []
    if text:
        _session_store[session_id].append(text)
        _last_chunk_text[session_id] = text
        _update_session_medicine_vocab(session_id, text)

    accumulated = _normalize_text(" ".join(_session_store[session_id]))
    _set_prompt_tail_for_session(session_id, accumulated)
    return JSONResponse(
        {
            "session_id": session_id,
            "chunk_index": chunk_index,
            "chunk_text": text,
            "accumulated_text": accumulated,
            "saved_audio_path": str(saved_path),
            "whisper_elapsed_seconds": whisper_elapsed_seconds,
        }
    )


@app.post("/clean-transcript")
async def clean_transcript(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse({"detail": f"Invalid cleaning request: {exc}"}, status_code=400)
    raw_text = _normalize_text(str((payload or {}).get("raw_text") or ""))
    session_id = _normalize_text(str((payload or {}).get("session_id") or ""))
    if not raw_text:
        return JSONResponse({"detail": "Missing raw_text"}, status_code=400)
    if session_id:
        if _session_cleaned_raw.get(session_id) == raw_text and _session_cleaned_text.get(session_id):
            return JSONResponse(
                {
                    "cleaned_text": _session_cleaned_text[session_id],
                    "clean_elapsed_seconds": 0.0,
                }
            )
    try:
        cleaned_text, clean_elapsed_seconds = await asyncio.wait_for(
            run_in_threadpool(_clean_transcript_with_llm, raw_text, session_id),
            timeout=CLEAN_REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"detail": f"Transcript cleaning timed out after {CLEAN_REQUEST_TIMEOUT_SECONDS:.1f}s"},
            status_code=504,
        )
    except Exception as exc:
        return JSONResponse({"detail": f"Ollama transcript cleaning failed: {exc}"}, status_code=503)
    if session_id:
        _session_cleaned_raw[session_id] = raw_text
        _session_cleaned_text[session_id] = cleaned_text
    return JSONResponse(
        {
            "cleaned_text": cleaned_text,
            "clean_elapsed_seconds": clean_elapsed_seconds,
        }
    )


@app.post("/clean-edit-field")
async def clean_edit_field(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse({"detail": f"Invalid edit cleaning request: {exc}"}, status_code=400)
    raw_text = _normalize_text(str((payload or {}).get("raw_text") or ""))
    field_name = _normalize_text(str((payload or {}).get("field_name") or ""))
    session_id = _normalize_text(str((payload or {}).get("session_id") or ""))
    if not raw_text:
        return JSONResponse({"detail": "Missing raw_text"}, status_code=400)
    if not field_name:
        return JSONResponse({"detail": "Missing field_name"}, status_code=400)
    try:
        cleaned_value, clean_elapsed_seconds = await asyncio.wait_for(
            run_in_threadpool(_clean_edit_field_value, field_name, raw_text, session_id),
            timeout=CLEAN_REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {"detail": f"Edit cleaning timed out after {CLEAN_REQUEST_TIMEOUT_SECONDS:.1f}s"},
            status_code=504,
        )
    except Exception as exc:
        return JSONResponse({"detail": f"Edit field cleaning failed: {exc}"}, status_code=503)
    return JSONResponse(
        {
            "field_name": field_name,
            "cleaned_value": cleaned_value,
            "clean_elapsed_seconds": clean_elapsed_seconds,
        }
    )


@app.post("/render-parchi")
async def render_parchi(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:
        return JSONResponse({"detail": f"Invalid parchi request: {exc}"}, status_code=400)
    cleaned_text = _normalize_text(str((payload or {}).get("cleaned_text") or ""))
    session_id = _normalize_text(str((payload or {}).get("session_id") or ""))
    if not cleaned_text and session_id:
        cleaned_text = _normalize_text(_session_cleaned_text.get(session_id, ""))
    if not cleaned_text:
        return JSONResponse({"detail": "Missing cleaned_text"}, status_code=400)
    try:
        parchi_payload, parchi_elapsed_seconds = await asyncio.wait_for(
            run_in_threadpool(_build_parchi_payload, cleaned_text),
            timeout=PARCHI_REQUEST_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        return JSONResponse(
            {
                "parchi": _fallback_parchi_payload(cleaned_text),
                "parchi_elapsed_seconds": 0.0,
                "warning": f"Parchi extraction timed out after {PARCHI_REQUEST_TIMEOUT_SECONDS:.1f}s",
            },
            status_code=200,
        )
    except Exception as exc:
        parchi_payload = _fallback_parchi_payload(cleaned_text)
        parchi_elapsed_seconds = 0.0
    return JSONResponse(
        {
            "parchi": parchi_payload,
            "parchi_elapsed_seconds": parchi_elapsed_seconds,
        }
    )


@app.post("/reset")
async def reset_session(request: Request):
    try:
        form = await request.form()
    except Exception as exc:
        return JSONResponse({"detail": f"Unable to parse reset form: {exc}"}, status_code=400)
    session_id = str(form.get("session_id") or "").strip()
    if not session_id:
        return JSONResponse({"detail": "Missing session_id"}, status_code=400)
    _session_store.pop(session_id, None)
    _chunk_counts.pop(session_id, None)
    _session_prompt_tail.pop(session_id, None)
    _session_medicine_vocab.pop(session_id, None)
    _last_chunk_text.pop(session_id, None)
    _session_cleaned_text.pop(session_id, None)
    _session_cleaned_raw.pop(session_id, None)
    return {"session_id": session_id, "reset": True}
