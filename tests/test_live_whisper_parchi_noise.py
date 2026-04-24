import importlib.util
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parent / "live_whisper_browser_stream.py"
SPEC = importlib.util.spec_from_file_location("live_whisper_browser_stream", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
live_whisper = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(live_whisper)
live_whisper.USE_LLM_STRUCTURER = False


def test_repeated_whisper_hallucination_tail_is_removed_before_parchi_extraction():
    raw = (
        "Patient complained about he has fever and I gave him Dolo, Crocin and Brofin "
        "and I told him to come after 5 days. Dolo 150, Pantop 40, Pantop 40, "
        "Pantop 40, Pantop 40, Pantop 40, Pantop 40, Pantop Oh oh oh oh oh oh oh oh"
    )

    cleaned = live_whisper._basic_medical_cleanup(raw)
    payload, _elapsed = live_whisper._build_parchi_payload(cleaned)

    medicine_names = [item["name"] for item in payload["medicines"]]
    assert cleaned == (
        "Patient complained about he has fever and I gave him Dolo, Crocin and Brofin "
        "and Follow-up: 5 days"
    )
    assert medicine_names == ["Dolo", "Crocin", "Brofin"]
    assert "Pantop 40" not in medicine_names
    assert "Dolo 150" not in medicine_names
    assert payload["patient_name"] == ""
    assert payload["complaints"] == "Fever"
    assert payload["follow_up"] == "5 days"
    assert all(item["duration"] == "" for item in payload["medicines"])


def test_patient_complained_is_not_used_as_patient_name():
    assert live_whisper._extract_patient_name("Patient complained about he has fever") == ""


def test_long_filler_repetition_is_low_value():
    assert live_whisper._is_low_value_transcript("Oh oh oh oh oh oh oh oh oh oh") is True


def test_simple_sentence_uses_fast_rule_cleaning_and_extracts_name():
    raw = (
        "Patient complained about fever and his name is Vineet and I gave him "
        "Dolo, Crocin, Brofin. And I told him to come after 3 days."
    )

    cleaned, elapsed = live_whisper._clean_transcript_with_llm(raw, "fast-rules")
    payload, _elapsed = live_whisper._build_parchi_payload(cleaned)

    assert elapsed == 0.0
    assert payload["patient_name"] == "Vineet"
    assert payload["complaints"] == "Fever"
    assert [item["name"] for item in payload["medicines"]] == ["Dolo", "Crocin", "Brofin"]
    assert payload["follow_up"] == "3 days"


def test_asr_grofin_is_normalized_to_brofin():
    assert live_whisper._extract_medicine_names("I gave him Dolo, Crocin, Grofin") == [
        "Dolo",
        "Crocin",
        "Brofin",
    ]


def test_llm_structurer_handles_unknown_terms_without_new_rules(monkeypatch):
    live_whisper.USE_LLM_STRUCTURER = True

    def fake_generate(_system_prompt, _user_prompt):
        return """
        {
          "patient_name": "Vineet",
          "complaints": "SADT",
          "diagnosis": "",
          "medicines": [
            {"name": "Pantop", "frequency": "", "timing": "", "duration": "", "notes": ""},
            {"name": "Rablet", "frequency": "", "timing": "", "duration": "", "notes": ""},
            {"name": "Nexpro", "frequency": "", "timing": "", "duration": "", "notes": ""}
          ],
          "vital_signs": {"blood_pressure": "", "temperature": "", "weight": ""},
          "advice": "",
          "tests": "",
          "follow_up": "4 days"
        }
        """

    monkeypatch.setattr(live_whisper._llm_client, "generate", fake_generate)
    payload, elapsed = live_whisper._build_parchi_payload(
        "Patient's name is Vineet and he complained about HDD and I gave him pantop, wrap, let, next probe and Follow-up: 4 days. It's SADT not HDD."
    )

    assert elapsed >= 0.0
    assert payload["patient_name"] == "Vineet"
    assert payload["complaints"] == "SADT"
    assert [item["name"] for item in payload["medicines"]] == ["Pantop", "Rablet", "Nexpro"]
    assert payload["follow_up"] == "4 days"
    live_whisper.USE_LLM_STRUCTURER = False


def test_vitals_and_diagnosis_are_preserved_by_rule_fallback():
    cleaned = live_whisper._basic_medical_cleanup(
        "Patient's name is Vinit and his BP is 40 by 120. "
        "He has high blood pleasure. So I gave him tells me Sartan."
    )
    payload, _elapsed = live_whisper._build_rule_based_parchi_payload(cleaned)

    assert payload["patient_name"] == "Vinit"
    assert payload["diagnosis"] == "High blood pressure"
    assert payload["vital_signs"]["blood_pressure"] == "40/120 mmHg"
    assert [item["name"] for item in payload["medicines"]] == ["Telmisartan"]


def test_whisper_prompt_uses_specific_medicine_vocabulary():
    prompt = live_whisper._prompt_tail_for_session("new-session")

    assert "Dolo 650" in prompt
    assert "Pantop 40" in prompt
    assert "Nexpro 40" in prompt
    assert "Rablet 20" in prompt
    assert "Telmisartan 40" in prompt


def test_session_prompt_tail_prefers_medicine_vocabulary_over_raw_tail():
    session_id = "vocab-session"

    live_whisper._update_session_medicine_vocab(session_id, "Patient took Dolo and Pantop 40")
    live_whisper._set_prompt_tail_for_session(session_id, "some unrelated long raw transcript tail")

    prompt = live_whisper._prompt_tail_for_session(session_id)
    assert "Previously mentioned in this session:" in prompt
    assert "Dolo" in prompt
    assert "Pantop 40" in prompt
    assert "unrelated long raw transcript tail" not in prompt
    live_whisper._session_medicine_vocab.pop(session_id, None)
    live_whisper._session_prompt_tail.pop(session_id, None)


def test_ambiguous_frequency_passes_context_window():
    frequency = live_whisper._extract_frequency_raw("Give Pantop before food twice weekly for 5 days")

    assert frequency.startswith("AMBIGUOUS:")
    assert "Pantop before food twice weekly for 5 days" in frequency
