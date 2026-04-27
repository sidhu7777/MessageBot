from __future__ import annotations

import argparse
import csv
import html
import io
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from dotenv import load_dotenv
import requests

from src.db.connection import connect_mysql, parse_mysql_url


TEXT_NONE_VALUES = {"", "-", "--", "na", "n/a", "null", "none", "unknown"}
OPENFDA_LABEL_URL = "https://api.fda.gov/drug/label.json"
DAILYMED_SPLS_URL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
RXNORM_PRESCRIBE_ALLCONCEPTS_URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/allconcepts.json"
RXNORM_FIND_RXCUI_URL = "https://rxnav.nlm.nih.gov/REST/Prescribe/rxcui.json"
RXNORM_PROPERTIES_URL = "https://rxnav.nlm.nih.gov/REST/rxcui/{rxcui}/properties.json"
PMBI_SORTING_URL = "https://www.pmbi.co.in/SortingView.aspx"
CDSCO_APPROVED_NEW_DRUGS_URL = "https://www.cdsco.gov.in/opencms/opencms/en/Approval_new/Approved-New-Drugs/"
INDIAN_MEDICINE_GITHUB_CSV_URL = "https://raw.githubusercontent.com/junioralive/Indian-Medicine-Dataset/main/DATA/indian_medicine_data.csv"
HTTP_TIMEOUT_SECONDS = max(10, int(os.getenv("MEDICINE_HTTP_TIMEOUT_SECONDS", "45")))
MAX_NAME_LEN = 255
MAX_STRENGTH_LEN = 120
MAX_FORM_LEN = 120
MAX_SOURCE_LEN = 120
MAX_DISPLAY_LEN = 255
MAX_MANUFACTURER_LEN = 255
PMBI_EXCLUDED_GROUPS = {
    "Surgical & Medical Consumables",
}
PMBI_NON_MEDICINE_KEYWORDS = {
    "belt",
    "catheter",
    "cannula",
    "syringe",
    "needle",
    "gauze",
    "dressing",
    "glove",
    "gloves",
    "mask",
    "extension line",
    "stopcock",
    "surgical tape",
    "cotton roll",
    "urine bag",
    "iv set",
    "nasal prongs",
    "bandage",
    "pulse oximeter",
    "walker",
    "wheelchair",
    "commode",
    "diaper",
}
FORM_ALIASES: tuple[tuple[str, str], ...] = (
    ("eye drops", "drops"),
    ("ear drops", "drops"),
    ("nasal drops", "drops"),
    ("oral drops", "drops"),
    ("drops", "drops"),
    ("tablets", "tablet"),
    ("tablet", "tablet"),
    ("capsules", "capsule"),
    ("capsule", "capsule"),
    ("softgel capsule", "capsule"),
    ("softgel capsules", "capsule"),
    ("injection", "injection"),
    ("injectable", "injection"),
    ("infusion", "infusion"),
    ("syrup", "syrup"),
    ("oral suspension", "suspension"),
    ("suspension", "suspension"),
    ("solution", "solution"),
    ("cream", "cream"),
    ("ointment", "ointment"),
    ("gel", "gel"),
    ("lotion", "lotion"),
    ("spray", "spray"),
    ("powder", "powder"),
    ("granules", "granules"),
    ("respules", "respules"),
    ("inhaler", "inhaler"),
    ("nebuliser solution", "solution"),
    ("soap", "soap"),
    ("shampoo", "shampoo"),
    ("mouthwash", "mouthwash"),
    ("lozenges", "lozenge"),
    ("lozenge", "lozenge"),
)
DOSAGE_DESCRIPTOR_PATTERN = re.compile(
    r"\b("
    r"ip|bp|usp|sr|er|xr|cr|mr|od|md|dt|ec|"+
    r"enteric coated|gastro resistant|gastro-resistant|prolonged release|"
    r"sustained release|extended release|immediate release|mouth dissolving|"
    r"film coated|chewable|dispersible|uncoated|modified release|"
    r"effervescent|probiotic|sterile|ready to use|ready-to-use"
    r")\b",
    flags=re.IGNORECASE,
)
PACK_SIZE_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:ml|g|gm|kg|mcg|mg|l|lt|tab|tabs|tablet|tablets|capsule|capsules|ampoule|ampoules|vial|vials|bottle|bottles)\b",
    flags=re.IGNORECASE,
)
STRENGTH_TOKEN_PATTERN = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|g|gm|ml|meq|iu|units?|%)"
    r"(?:\s*/\s*(?:ml|g|gm|l))?"
    r"(?:\s*(?:w/v|w/w|v/v))?\b",
    flags=re.IGNORECASE,
)
COMMON_INDIAN_MEDICINE_TERMS = [
    "Dolo 650", "Crocin 650", "Crocin 500", "Calpol 650", "Paracetamol 650",
    "Pantop 40", "Pantoprazole 40", "Nexpro 40", "Nexium 40", "Rablet 20",
    "Rabeprazole 20", "Omeprazole 20", "Pan-D", "Pan D", "Domperidone 10",
    "Azithral 500", "Azithromycin 500", "Augmentin 625", "Amoxicillin 500",
    "Amoxycillin 500", "Ciprofloxacin 500", "Cetirizine 10", "Cetrizine 10",
    "Telmisartan 40", "Telma 40", "Amlodipine 5", "Metformin 500",
    "Atorvastatin 10", "Rosuvastatin 10", "Ecosprin 75", "Montair LC",
    "Montair 10", "Montek LC", "Levocetirizine 5", "Allegra 120",
    "Norflox TZ", "Ofloxacin 200", "Ondem 4", "Ondansetron 4",
    "Brufen 400", "Brofin 400", "Zerodol SP", "Aceclofenac 100",
    "Meftal Spas", "Meftal 500", "Taxim O 200", "Cefixime 200",
    "Sinarest", "Almox 500", "Clavam 625", "Monocef O 200",
]


@dataclass(frozen=True)
class MedicineRecord:
    canonical_name: str
    brand_name: str
    generic_name: str
    strength: str
    form: str
    manufacturer: str
    source_name: str
    source_id: str
    raw_payload_json: str

    @property
    def display_name(self) -> str:
        parts = [self.brand_name or self.canonical_name, self.strength, self.form]
        return _truncate_text(" ".join(part for part in parts if part).strip(), MAX_DISPLAY_LEN)

    @property
    def canonical_name_norm(self) -> str:
        return _normalize_key(self.canonical_name)

    @property
    def brand_name_norm(self) -> str:
        return _normalize_key(self.brand_name)

    @property
    def generic_name_norm(self) -> str:
        return _normalize_key(self.generic_name)

    @property
    def strength_norm(self) -> str:
        return _normalize_key(self.strength)

    @property
    def form_norm(self) -> str:
        return _normalize_key(self.form)


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    collapsed = " ".join(text.replace("\n", " ").replace("\r", " ").split())
    if collapsed.lower() in TEXT_NONE_VALUES:
        return ""
    return collapsed


def _truncate_text(value: Any, max_len: int) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    return text[:max_len].strip()


def _normalize_key(value: Any) -> str:
    text = _normalize_text(value).lower()
    cleaned = []
    for ch in text:
        if ch.isalnum():
            cleaned.append(ch)
        elif ch in {" ", "+", "/", "-", "."}:
            cleaned.append(" ")
    return " ".join("".join(cleaned).split())


def _first_non_empty(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in payload:
            text = _normalize_text(payload.get(key))
            if text:
                return text
    return ""


def _pick_first_list_value(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, list):
            for item in value:
                text = _normalize_text(item)
                if text:
                    return text
        else:
            text = _normalize_text(value)
            if text:
                return text
    return ""


def _extract_openfda_strength(payload: dict[str, Any]) -> str:
    for key in ("active_ingredient", "active_ingredients", "dosage_and_administration"):
        value = payload.get(key)
        if isinstance(value, list):
            joined = " ".join(_normalize_text(item) for item in value if _normalize_text(item))
            if joined:
                return joined[:120]
        text = _normalize_text(value)
        if text:
            return text[:120]
    return ""


def _extract_strength_and_form_from_name(name: str) -> tuple[str, str]:
    normalized = _normalize_text(name)
    if not normalized:
        return "", ""
    strengths: list[str] = []
    for match in STRENGTH_TOKEN_PATTERN.findall(normalized):
        token = _normalize_text(match).lower()
        if token and token not in strengths:
            strengths.append(token)
    strength = " + ".join(strengths[:6])
    lowered = normalized.lower()
    form = ""
    for needle, normalized_form in FORM_ALIASES:
        if needle in lowered:
            form = normalized_form
            break
    return strength, form


def _parse_rxnorm_display_name(name: str) -> tuple[str, str, str, str]:
    normalized = _normalize_text(name)
    if not normalized:
        return "", "", "", ""
    brand_name = ""
    bracket_match = re.search(r"\[([^\]]+)\]", normalized)
    if bracket_match:
        brand_name = _normalize_text(bracket_match.group(1))
        normalized = _normalize_text(re.sub(r"\[[^\]]+\]", "", normalized))
    strength, form = _extract_strength_and_form_from_name(normalized)
    canonical = normalized
    if strength:
        canonical = re.sub(
            r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml|units|unt|meq|iu|%)\b",
            "",
            canonical,
            flags=re.IGNORECASE,
        )
    if form:
        canonical = re.sub(rf"\b{re.escape(form)}\b", "", canonical, flags=re.IGNORECASE)
    canonical = re.sub(
        r"\b(oral|topical|nasal|ophthalmic|otic|injectable|extended release|delayed release|chewable|film coated)\b",
        "",
        canonical,
        flags=re.IGNORECASE,
    )
    canonical = " ".join(canonical.replace("/", " ").split()).strip(" ,;-")
    return canonical, brand_name, strength, form


def _infer_form_from_term(name: str) -> str:
    lowered = _normalize_text(name).lower()
    if "pan d" in lowered or "spas" in lowered or "tz" in lowered or "lc" in lowered:
        return "tablet"
    return ""


def _clean_brand_product_name(name: str) -> str:
    text = _clean_phrase_punctuation(name)
    if not text:
        return ""
    for needle, _normalized_form in FORM_ALIASES:
        text = re.sub(rf"\b{re.escape(needle)}\b", " ", text, flags=re.IGNORECASE)
    text = DOSAGE_DESCRIPTOR_PATTERN.sub(" ", text)
    text = re.sub(r"\bip\b|\bbp\b|\busp\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" ,;+")


def _clean_phrase_punctuation(value: str) -> str:
    text = _normalize_text(value)
    if not text:
        return ""
    text = html.unescape(text)
    text = text.replace("a-ß", "alpha beta")
    text = text.replace("β", "beta")
    text = text.replace("ß", "beta")
    text = text.replace("α", "alpha")
    text = re.sub(r"[+/]", " + ", text)
    text = text.replace(",", " + ")
    text = text.replace("(", " ").replace(")", " ")
    text = text.replace("[", " ").replace("]", " ")
    text = text.replace("'", "")
    return " ".join(text.split()).strip(" ,;-")


def _clean_generic_ingredient_phrase(name: str) -> str:
    text = _clean_phrase_punctuation(name)
    if not text:
        return ""
    text = DOSAGE_DESCRIPTOR_PATTERN.sub(" ", text)
    text = STRENGTH_TOKEN_PATTERN.sub(" ", text)
    for needle, _normalized_form in FORM_ALIASES:
        text = re.sub(rf"\b{re.escape(needle)}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bampoule(?:s)?\b|\bvial(?:s)?\b|\bdrops\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bip\b|\bbp\b|\busp\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\b\d+\s*(?:x|by)\s*\d+\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bwith\b", " + ", text, flags=re.IGNORECASE)
    text = re.sub(r"\band\b", " + ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+\+\s+", " + ", text)
    text = re.sub(r"\b(?:forte|plus|junior|kids|adult)\b", " ", text, flags=re.IGNORECASE)
    return " ".join(text.split()).strip(" ,;+")


def _clean_canonical_medicine_name(name: str) -> str:
    text = _clean_phrase_punctuation(name)
    if not text:
        return ""
    text = DOSAGE_DESCRIPTOR_PATTERN.sub(" ", text)
    for needle, _normalized_form in FORM_ALIASES:
        text = re.sub(rf"\b{re.escape(needle)}\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\bampoule(?:s)?\b|\bvial(?:s)?\b", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"\s+\+\s+", " + ", text)
    return " ".join(text.split()).strip(" ,;+")


def _is_probable_non_medicine_product(name: str, *, group_name: str) -> bool:
    lowered_name = _normalize_key(name)
    lowered_group = _normalize_key(group_name)
    if group_name in PMBI_EXCLUDED_GROUPS:
        return True
    if "consumable" in lowered_group or "device" in lowered_group:
        return True
    return any(keyword in lowered_name for keyword in PMBI_NON_MEDICINE_KEYWORDS)


def _build_rxnorm_ingredient_index(session: requests.Session) -> dict[str, str]:
    try:
        response = session.get(
            RXNORM_PRESCRIBE_ALLCONCEPTS_URL,
            params={"tty": "IN PIN MIN"},
            timeout=max(60, HTTP_TIMEOUT_SECONDS),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return {}
    group = payload.get("minConceptGroup") if isinstance(payload, dict) else {}
    concepts = group.get("minConcept") if isinstance(group, dict) else None
    if not isinstance(concepts, list):
        return {}
    index: dict[str, str] = {}
    for item in concepts:
        if not isinstance(item, dict):
            continue
        name = _normalize_text(item.get("name"))
        if not name:
            continue
        index.setdefault(_normalize_key(name), name)
    return index


def _normalize_name_with_rxnorm_index(name: str, rxnorm_index: dict[str, str]) -> str:
    lookup_name = _normalize_text(name)
    if not lookup_name:
        return ""
    matched_name = rxnorm_index.get(_normalize_key(lookup_name))
    if not matched_name:
        return lookup_name
    return lookup_name


def _build_record(payload: dict[str, Any], *, source_name: str, default_source_id: str) -> Optional[MedicineRecord]:
    raw_record = payload.get("raw_record")
    if isinstance(raw_record, dict):
        payload = dict(raw_record)
    brand_name = _first_non_empty(
        payload,
        (
            "brand_name",
            "brand",
            "product_name",
            "product",
            "trade_name",
            "name",
            "drug_name",
            "medicine_name",
        ),
    )
    generic_name = _first_non_empty(
        payload,
        (
            "generic_name",
            "generic",
            "ingredient",
            "active_ingredient",
            "salt_name",
            "salt",
            "composition",
            "molecule",
        ),
    )
    strength = _first_non_empty(
        payload,
        (
            "strength",
            "dose",
            "dosage_strength",
            "dosage",
            "strength_value",
        ),
    )
    form = _first_non_empty(
        payload,
        (
            "form",
            "dosage_form",
            "drug_form",
            "medicine_form",
            "presentation",
        ),
    )
    manufacturer = _first_non_empty(
        payload,
        (
            "manufacturer",
            "labeler",
            "company",
            "organization",
            "marketer",
        ),
    )
    canonical_name = _first_non_empty(
        payload,
        (
            "canonical_name",
            "display_name",
            "normalized_name",
        ),
    )
    if not canonical_name and source_name.strip().lower() == "rxnorm":
        rx_name = _first_non_empty(payload, ("name",))
        parsed_canonical, parsed_brand, parsed_strength, parsed_form = _parse_rxnorm_display_name(rx_name)
        canonical_name = parsed_canonical
        if not brand_name:
            brand_name = parsed_brand
        if not strength:
            strength = parsed_strength
        if not form:
            form = parsed_form
    if not canonical_name:
        canonical_name = brand_name or generic_name
    if not generic_name and canonical_name:
        generic_name = canonical_name
    if not canonical_name:
        return None
    source_id = _first_non_empty(
        payload,
        (
            "source_id",
            "id",
            "rxcui",
            "setid",
            "spl_set_id",
            "drug_id",
            "product_id",
        ),
    ) or default_source_id
    normalized_payload = {str(key): value for key, value in payload.items()}
    return MedicineRecord(
        canonical_name=_truncate_text(canonical_name, MAX_NAME_LEN),
        brand_name=_truncate_text(brand_name, MAX_NAME_LEN),
        generic_name=_truncate_text(generic_name, MAX_NAME_LEN),
        strength=_truncate_text(strength, MAX_STRENGTH_LEN),
        form=_truncate_text(form, MAX_FORM_LEN),
        manufacturer=_truncate_text(manufacturer, MAX_MANUFACTURER_LEN),
        source_name=_truncate_text(source_name, MAX_SOURCE_LEN),
        source_id=_truncate_text(source_id, MAX_SOURCE_LEN),
        raw_payload_json=json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True),
    )


def _read_csv_like(path: Path, *, delimiter: str) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        for row in reader:
            yield dict(row or {})


def _read_json_array(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
    elif isinstance(payload, dict):
        for key in ("data", "items", "results", "drugs", "medicines"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return
        yield payload


def _read_json_lines(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            item = json.loads(raw)
            if isinstance(item, dict):
                yield item


def _iter_source_rows(path: Path, *, delimiter: str) -> Iterator[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield from _read_csv_like(path, delimiter=delimiter)
        return
    if suffix == ".tsv":
        yield from _read_csv_like(path, delimiter="\t")
        return
    if suffix == ".jsonl":
        yield from _read_json_lines(path)
        return
    if suffix == ".json":
        yield from _read_json_array(path)
        return
    raise ValueError(f"Unsupported file type: {path.name}")


def _fetch_json(url: str, *, params: dict[str, Any]) -> dict[str, Any]:
    response = requests.get(url, params=params, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected JSON payload from {url}")
    return payload


def _iter_openfda_rows(*, limit: int, batch_size: int) -> Iterator[dict[str, Any]]:
    fetched = 0
    skip = 0
    while fetched < limit:
        current_batch = min(batch_size, limit - fetched)
        payload = _fetch_json(
            OPENFDA_LABEL_URL,
            params={"limit": current_batch, "skip": skip},
        )
        results = payload.get("results")
        if not isinstance(results, list) or not results:
            break
        for item in results:
            if not isinstance(item, dict):
                continue
            openfda = item.get("openfda") if isinstance(item.get("openfda"), dict) else {}
            yield {
                "brand_name": _pick_first_list_value(openfda, ("brand_name", "substance_name")),
                "generic_name": _pick_first_list_value(openfda, ("generic_name", "substance_name")),
                "manufacturer": _pick_first_list_value(openfda, ("manufacturer_name",)),
                "form": _pick_first_list_value(openfda, ("dosage_form", "route")),
                "strength": _extract_openfda_strength(item),
                "source_id": _pick_first_list_value(openfda, ("product_ndc", "spl_set_id", "package_ndc")),
                "raw_record": {
                    "brand_name": _pick_first_list_value(openfda, ("brand_name", "substance_name")),
                    "generic_name": _pick_first_list_value(openfda, ("generic_name", "substance_name")),
                    "manufacturer": _pick_first_list_value(openfda, ("manufacturer_name",)),
                    "form": _pick_first_list_value(openfda, ("dosage_form", "route")),
                    "strength": _extract_openfda_strength(item),
                    "source_id": _pick_first_list_value(openfda, ("product_ndc", "spl_set_id", "package_ndc")),
                    "openfda_raw": item,
                },
            }
        count = len(results)
        fetched += count
        skip += count
        if count < current_batch:
            break


def _iter_dailymed_rows(*, limit: int, batch_size: int) -> Iterator[dict[str, Any]]:
    fetched = 0
    page = 1
    while fetched < limit:
        current_batch = min(batch_size, limit - fetched)
        payload = _fetch_json(
            DAILYMED_SPLS_URL,
            params={"pagesize": current_batch, "page": page},
        )
        data = payload.get("data")
        if not isinstance(data, list) or not data:
            break
        for item in data:
            if not isinstance(item, dict):
                continue
            yield {
                "raw_record": {
                    "brand_name": _normalize_text(item.get("title")),
                    "generic_name": _normalize_text(item.get("generic_name")),
                    "manufacturer": _normalize_text(item.get("labeler_name")),
                    "form": _normalize_text(item.get("dosage_form")),
                    "strength": _normalize_text(item.get("strength")),
                    "source_id": _normalize_text(item.get("setid") or item.get("spl_set_id")),
                    "dailymed_raw": item,
                }
            }
        count = len(data)
        fetched += count
        page += 1
        if count < current_batch:
            break


def _iter_rxnorm_rows(*, limit: int) -> Iterator[dict[str, Any]]:
    payload = _fetch_json(
        RXNORM_PRESCRIBE_ALLCONCEPTS_URL,
        params={"tty": "BN IN PIN MIN SCD SBD GPCK BPCK"},
    )
    group = payload.get("minConceptGroup")
    concepts = group.get("minConcept") if isinstance(group, dict) else None
    if not isinstance(concepts, list):
        return
    yielded = 0
    for item in concepts:
        if not isinstance(item, dict):
            continue
        name = _normalize_text(item.get("name"))
        if not name:
            continue
        tty = _normalize_text(item.get("tty")).upper()
        canonical_name, parsed_brand_name, strength, form = _parse_rxnorm_display_name(name)
        if not canonical_name:
            continue
        if tty in {"BN", "BPCK", "SBD"}:
            brand_name = parsed_brand_name or canonical_name
            generic_name = canonical_name
        else:
            brand_name = parsed_brand_name
            generic_name = canonical_name
        yield {
            "raw_record": {
                "canonical_name": canonical_name,
                "brand_name": brand_name,
                "generic_name": generic_name,
                "strength": strength,
                "form": form,
                "name": name,
                "source_id": _normalize_text(item.get("rxcui")),
                "term_type": tty,
                "rxnorm_raw": item,
            }
        }
        yielded += 1
        if yielded >= limit:
            break


def _iter_builtin_indian_common_rows() -> Iterator[dict[str, Any]]:
    for idx, term in enumerate(COMMON_INDIAN_MEDICINE_TERMS, start=1):
        strength, form = _extract_strength_and_form_from_name(term)
        canonical = term
        if strength:
            canonical = re.sub(
                r"\b\d+(?:\.\d+)?\s*(mg|mcg|g|ml|units|unt|meq|iu|%)\b",
                "",
                canonical,
                flags=re.IGNORECASE,
            )
        canonical = " ".join(canonical.split()).strip(" ,;-")
        yield {
            "raw_record": {
                "canonical_name": canonical,
                "brand_name": term,
                "generic_name": canonical,
                "strength": strength,
                "form": form or _infer_form_from_term(term),
                "source_id": f"indian-common:{idx}",
                "seed_group": "indian-common",
            }
        }


def _extract_hidden_fields(html_text: str) -> dict[str, str]:
    hidden_fields: dict[str, str] = {}
    for match in re.finditer(
        r'<input[^>]+type="hidden"[^>]+name="([^"]+)"[^>]+value="([^"]*)"',
        html_text,
        re.IGNORECASE,
    ):
        hidden_fields[match.group(1)] = html.unescape(match.group(2))
    return hidden_fields


def _parse_pmbi_table_rows(html_text: str) -> Iterator[dict[str, str]]:
    table_match = re.search(
        r'<table[^>]*class="table table-striped table-bordered table-hover"[^>]*>(.*?)</table>',
        html_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not table_match:
        return
    table_html = table_match.group(1)
    row_matches = re.findall(
        r"<tr>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"<td[^>]*>(.*?)</td>\s*"
        r"</tr>",
        table_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for sr_no, drug_code, generic_name, unit_size, mrp, group_name in row_matches:
        def clean_cell(value: str) -> str:
            plain = re.sub(r"<[^>]+>", " ", value)
            return _normalize_text(html.unescape(plain).replace("\xa0", " "))

        yield {
            "sr_no": clean_cell(sr_no),
            "drug_code": clean_cell(drug_code),
            "generic_name": clean_cell(generic_name),
            "unit_size": clean_cell(unit_size),
            "mrp": clean_cell(mrp),
            "group_name": clean_cell(group_name),
        }


def _iter_pmbi_rows(*, rxnorm_normalize: bool = True) -> Iterator[dict[str, Any]]:
    session = requests.Session()
    initial_response = session.get(PMBI_SORTING_URL, timeout=HTTP_TIMEOUT_SECONDS)
    initial_response.raise_for_status()
    hidden_fields = _extract_hidden_fields(initial_response.text)
    payload = {
        "__VIEWSTATE": hidden_fields.get("__VIEWSTATE", ""),
        "__VIEWSTATEGENERATOR": hidden_fields.get("__VIEWSTATEGENERATOR", ""),
        "__EVENTVALIDATION": hidden_fields.get("__EVENTVALIDATION", ""),
        "ctl00$Bppi_body$ddlProduct": "GenericName",
        "ctl00$Bppi_body$btnSearch": "Search",
    }
    result_response = session.post(PMBI_SORTING_URL, data=payload, timeout=max(60, HTTP_TIMEOUT_SECONDS))
    result_response.raise_for_status()
    rxnorm_index = _build_rxnorm_ingredient_index(session) if rxnorm_normalize else {}
    for row in _parse_pmbi_table_rows(result_response.text):
        generic_name = row.get("generic_name", "")
        unit_size = row.get("unit_size", "")
        if generic_name and unit_size:
            generic_name = re.sub(
                rf"(?:,|\s)\s*{re.escape(unit_size)}\s*$",
                "",
                generic_name,
                flags=re.IGNORECASE,
            ).strip(" ,;-")
        group_name = row.get("group_name", "")
        if not generic_name or _is_probable_non_medicine_product(generic_name, group_name=group_name):
            continue
        canonical_name = _clean_canonical_medicine_name(generic_name)
        ingredient_name = _clean_generic_ingredient_phrase(generic_name)
        if rxnorm_normalize and ingredient_name:
            ingredient_name = _normalize_name_with_rxnorm_index(ingredient_name, rxnorm_index)
        strength, form = _extract_strength_and_form_from_name(generic_name)
        if not canonical_name:
            canonical_name = ingredient_name or generic_name
        if not ingredient_name:
            ingredient_name = canonical_name
        yield {
            "raw_record": {
                "canonical_name": canonical_name,
                "generic_name": ingredient_name,
                "strength": strength,
                "form": form,
                "manufacturer": "PMBI",
                "source_id": row.get("drug_code") or row.get("sr_no"),
                "unit_size": unit_size,
                "mrp": row.get("mrp"),
                "group_name": group_name,
                "source_page": PMBI_SORTING_URL,
                "cdsco_reference_page": CDSCO_APPROVED_NEW_DRUGS_URL,
                "pmbi_raw": row,
            }
        }


def _iter_github_indian_dataset_rows() -> Iterator[dict[str, Any]]:
    response = requests.get(INDIAN_MEDICINE_GITHUB_CSV_URL, timeout=max(120, HTTP_TIMEOUT_SECONDS))
    response.raise_for_status()
    csv_buffer = io.StringIO(response.text)
    reader = csv.DictReader(csv_buffer)
    for row in reader:
        payload = dict(row or {})
        product_type = _normalize_text(payload.get("type")).lower()
        if product_type and product_type != "allopathy":
            continue
        if _normalize_text(payload.get("Is_discontinued")).upper() == "TRUE":
            continue
        product_name = _normalize_text(payload.get("name"))
        if not product_name:
            continue
        short_comp_1 = _normalize_text(payload.get("short_composition1"))
        short_comp_2 = _normalize_text(payload.get("short_composition2"))
        composition_parts = [part for part in (short_comp_1, short_comp_2) if part]
        if not composition_parts:
            continue
        composition_phrase = " + ".join(composition_parts)
        generic_name = _clean_generic_ingredient_phrase(composition_phrase)
        strength, inferred_form = _extract_strength_and_form_from_name(composition_phrase)
        pack_size_label = _normalize_text(payload.get("pack_size_label"))
        form_from_pack = _extract_strength_and_form_from_name(pack_size_label)[1]
        form_from_name = _extract_strength_and_form_from_name(product_name)[1]
        form = form_from_pack or form_from_name or inferred_form
        canonical_name = _clean_brand_product_name(product_name)
        if not canonical_name:
            canonical_name = product_name
        yield {
            "raw_record": {
                "canonical_name": canonical_name,
                "brand_name": product_name,
                "generic_name": generic_name or canonical_name,
                "strength": strength,
                "form": form,
                "manufacturer": _normalize_text(payload.get("manufacturer_name")),
                "source_id": _normalize_text(payload.get("id")),
                "price_inr": _normalize_text(payload.get("price(₹)")),
                "pack_size_label": pack_size_label,
                "type": product_type or "allopathy",
                "github_dataset_url": INDIAN_MEDICINE_GITHUB_CSV_URL,
                "cdsco_reference_page": CDSCO_APPROVED_NEW_DRUGS_URL,
                "source_page": "https://github.com/junioralive/Indian-Medicine-Dataset",
                "github_raw": payload,
            }
        }


def _iter_india_hybrid_rows() -> Iterator[dict[str, Any]]:
    yield from _iter_pmbi_rows(rxnorm_normalize=True)
    yield from _iter_github_indian_dataset_rows()


def _ensure_schema(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS medicine_master (
                medicine_id BIGINT NOT NULL AUTO_INCREMENT,
                canonical_name VARCHAR(255) NOT NULL,
                canonical_name_norm VARCHAR(255) NOT NULL,
                brand_name VARCHAR(255) NULL,
                brand_name_norm VARCHAR(255) NULL,
                generic_name VARCHAR(255) NULL,
                generic_name_norm VARCHAR(255) NULL,
                strength VARCHAR(120) NULL,
                strength_norm VARCHAR(120) NULL,
                form VARCHAR(120) NULL,
                form_norm VARCHAR(120) NULL,
                manufacturer VARCHAR(255) NULL,
                display_name VARCHAR(255) NOT NULL,
                source_name VARCHAR(120) NOT NULL,
                source_id VARCHAR(120) NULL,
                raw_payload_json LONGTEXT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                PRIMARY KEY (medicine_id),
                UNIQUE KEY uq_medicine_identity (
                    canonical_name_norm,
                    generic_name_norm,
                    strength_norm,
                    form_norm
                ),
                KEY idx_brand_name_norm (brand_name_norm),
                KEY idx_generic_name_norm (generic_name_norm),
                KEY idx_display_name (display_name)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS medicine_import_runs (
                import_id BIGINT NOT NULL AUTO_INCREMENT,
                source_name VARCHAR(120) NOT NULL,
                source_file VARCHAR(500) NOT NULL,
                row_count INT NOT NULL DEFAULT 0,
                inserted_count INT NOT NULL DEFAULT 0,
                updated_count INT NOT NULL DEFAULT 0,
                skipped_count INT NOT NULL DEFAULT 0,
                started_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME NULL,
                PRIMARY KEY (import_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
            """
        )
        conn.commit()
    finally:
        cur.close()


def _start_import_run(conn, *, source_name: str, source_file: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO medicine_import_runs (source_name, source_file)
            VALUES (%s, %s)
            """,
            (source_name, source_file),
        )
        conn.commit()
        return int(cur.lastrowid)
    finally:
        cur.close()


def _finish_import_run(
    conn,
    *,
    import_id: int,
    row_count: int,
    inserted_count: int,
    updated_count: int,
    skipped_count: int,
) -> None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE medicine_import_runs
            SET row_count = %s,
                inserted_count = %s,
                updated_count = %s,
                skipped_count = %s,
                finished_at = %s
            WHERE import_id = %s
            """,
            (
                row_count,
                inserted_count,
                updated_count,
                skipped_count,
                datetime.now(),
                import_id,
            ),
        )
        conn.commit()
    finally:
        cur.close()


def _upsert_record(conn, record: MedicineRecord) -> str:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT medicine_id
            FROM medicine_master
            WHERE canonical_name_norm = %s
              AND generic_name_norm = %s
              AND strength_norm = %s
              AND form_norm = %s
            LIMIT 1
            """,
            (
                record.canonical_name_norm,
                record.generic_name_norm,
                record.strength_norm,
                record.form_norm,
            ),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                """
                UPDATE medicine_master
                SET canonical_name = %s,
                    brand_name = %s,
                    brand_name_norm = %s,
                    generic_name = %s,
                    manufacturer = %s,
                    display_name = %s,
                    source_name = %s,
                    source_id = %s,
                    raw_payload_json = %s
                WHERE medicine_id = %s
                """,
                (
                    record.canonical_name,
                    record.brand_name or None,
                    record.brand_name_norm or None,
                    record.generic_name or None,
                    record.manufacturer or None,
                    record.display_name,
                    record.source_name,
                    record.source_id or None,
                    record.raw_payload_json,
                    int(row[0]),
                ),
            )
            conn.commit()
            return "updated"
        cur.execute(
            """
            INSERT INTO medicine_master (
                canonical_name,
                canonical_name_norm,
                brand_name,
                brand_name_norm,
                generic_name,
                generic_name_norm,
                strength,
                strength_norm,
                form,
                form_norm,
                manufacturer,
                display_name,
                source_name,
                source_id,
                raw_payload_json
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                record.canonical_name,
                record.canonical_name_norm,
                record.brand_name or None,
                record.brand_name_norm or None,
                record.generic_name or None,
                record.generic_name_norm or None,
                record.strength or None,
                record.strength_norm or None,
                record.form or None,
                record.form_norm or None,
                record.manufacturer or None,
                record.display_name,
                record.source_name,
                record.source_id or None,
                record.raw_payload_json,
            ),
        )
        conn.commit()
        return "inserted"
    finally:
        cur.close()


def _purge_rows_by_source(conn, *, source_name: str) -> int:
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM medicine_master WHERE source_name = %s",
            (_truncate_text(source_name, MAX_SOURCE_LEN),),
        )
        deleted = int(cur.rowcount or 0)
        conn.commit()
        return deleted
    finally:
        cur.close()


def _reset_medicine_master_table(conn) -> None:
    cur = conn.cursor()
    try:
        cur.execute("TRUNCATE TABLE medicine_master")
        conn.commit()
    finally:
        cur.close()


def run_import(
    *,
    source_file: Path,
    source_name: str,
    delimiter: str = ",",
    dry_run: bool = False,
) -> dict[str, int]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL_medicine", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL_medicine is not set in .env or environment")
    if not database_url.startswith("mysql+mysqlconnector://"):
        raise RuntimeError("DATABASE_URL_medicine must start with mysql+mysqlconnector://")
    config = parse_mysql_url(database_url)
    conn = connect_mysql(config)
    try:
        _ensure_schema(conn)
        import_id = _start_import_run(conn, source_name=source_name, source_file=str(source_file.resolve()))
        row_count = 0
        inserted_count = 0
        updated_count = 0
        skipped_count = 0
        default_source_prefix = f"{source_name}:{source_file.name}"
        for row_count, payload in enumerate(_iter_source_rows(source_file, delimiter=delimiter), start=1):
            record = _build_record(
                dict(payload or {}),
                source_name=source_name,
                default_source_id=f"{default_source_prefix}:{row_count}",
            )
            if record is None:
                skipped_count += 1
                continue
            if dry_run:
                inserted_count += 1
                continue
            result = _upsert_record(conn, record)
            if result == "inserted":
                inserted_count += 1
            else:
                updated_count += 1
        _finish_import_run(
            conn,
            import_id=import_id,
            row_count=row_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
        )
        return {
            "rows": row_count,
            "inserted": inserted_count,
            "updated": updated_count,
            "skipped": skipped_count,
        }
    finally:
        conn.close()


def run_api_import(
    *,
    source_name: str,
    source_api: str,
    limit: int,
    batch_size: int,
    replace_source: bool = False,
    dry_run: bool = False,
) -> dict[str, int]:
    load_dotenv()
    database_url = os.getenv("DATABASE_URL_medicine", "").strip()
    if not database_url:
        raise RuntimeError("DATABASE_URL_medicine is not set in .env or environment")
    if not database_url.startswith("mysql+mysqlconnector://"):
        raise RuntimeError("DATABASE_URL_medicine must start with mysql+mysqlconnector://")
    config = parse_mysql_url(database_url)
    conn = connect_mysql(config)
    try:
        _ensure_schema(conn)
        if replace_source and not dry_run:
            _purge_rows_by_source(conn, source_name=source_name)
        import_id = _start_import_run(conn, source_name=source_name, source_file=f"api:{source_api}")
        row_count = 0
        inserted_count = 0
        updated_count = 0
        skipped_count = 0
        normalized_api = source_api.strip().lower()
        if normalized_api == "openfda":
            rows = _iter_openfda_rows(limit=limit, batch_size=batch_size)
        elif normalized_api == "dailymed":
            rows = _iter_dailymed_rows(limit=limit, batch_size=batch_size)
        elif normalized_api == "rxnorm":
            rows = _iter_rxnorm_rows(limit=limit)
        elif normalized_api == "pmbi":
            rows = _iter_pmbi_rows(rxnorm_normalize=True)
        elif normalized_api == "india-github":
            rows = _iter_github_indian_dataset_rows()
        elif normalized_api == "india-hybrid":
            rows = _iter_india_hybrid_rows()
        elif normalized_api == "indian-common":
            rows = _iter_builtin_indian_common_rows()
        else:
            raise RuntimeError(f"Unsupported source API: {source_api}")
        for row_count, payload in enumerate(rows, start=1):
            record = _build_record(
                dict(payload or {}),
                source_name=source_name,
                default_source_id=f"{normalized_api}:{row_count}",
            )
            if record is None:
                skipped_count += 1
                continue
            if dry_run:
                inserted_count += 1
                continue
            result = _upsert_record(conn, record)
            if result == "inserted":
                inserted_count += 1
            else:
                updated_count += 1
        _finish_import_run(
            conn,
            import_id=import_id,
            row_count=row_count,
            inserted_count=inserted_count,
            updated_count=updated_count,
            skipped_count=skipped_count,
        )
        return {
            "rows": row_count,
            "inserted": inserted_count,
            "updated": updated_count,
            "skipped": skipped_count,
        }
    finally:
        conn.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Load bulk medicine data into the standalone medicine MySQL database."
    )
    parser.add_argument(
        "--source-file",
        help="Path to input data file (.csv, .tsv, .json, .jsonl).",
    )
    parser.add_argument(
        "--source-api",
        choices=("openfda", "dailymed", "rxnorm", "pmbi", "india-github", "india-hybrid", "indian-common"),
        help="Fetch directly from an open public API instead of a local file.",
    )
    parser.add_argument(
        "--source-name",
        default="manual-import",
        help="Logical source label stored with each imported row.",
    )
    parser.add_argument(
        "--delimiter",
        default=",",
        help="CSV delimiter for .csv files. Default is ','.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse rows and build records without writing to the database.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1000,
        help="Maximum API rows to fetch when --source-api is used. Default 1000.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        help="API page size when --source-api is used. Default 100.",
    )
    parser.add_argument(
        "--purge-source",
        help="Delete existing medicine_master rows for the given source_name before exit.",
    )
    parser.add_argument(
        "--reset-table",
        action="store_true",
        help="Truncate medicine_master before exit so IDs restart from 1 on the next load.",
    )
    parser.add_argument(
        "--replace-source",
        action="store_true",
        help="Delete existing rows for --source-name before importing the new source run.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    source_name = str(args.source_name or "manual-import").strip() or "manual-import"
    if args.reset_table:
        load_dotenv()
        database_url = os.getenv("DATABASE_URL_medicine", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL_medicine is not set in .env or environment")
        config = parse_mysql_url(database_url)
        conn = connect_mysql(config)
        try:
            _reset_medicine_master_table(conn)
        finally:
            conn.close()
        print(json.dumps({"reset_table": True}, indent=2))
        return 0
    if args.purge_source:
        load_dotenv()
        database_url = os.getenv("DATABASE_URL_medicine", "").strip()
        if not database_url:
            raise RuntimeError("DATABASE_URL_medicine is not set in .env or environment")
        config = parse_mysql_url(database_url)
        conn = connect_mysql(config)
        try:
            deleted = _purge_rows_by_source(conn, source_name=str(args.purge_source))
        finally:
            conn.close()
        print(json.dumps({"purged_source": str(args.purge_source), "deleted": deleted}, indent=2))
        return 0
    if args.source_api:
        summary = run_api_import(
            source_name=source_name,
            source_api=str(args.source_api),
            limit=max(1, int(args.limit)),
            batch_size=max(1, min(100, int(args.batch_size))),
            replace_source=bool(args.replace_source),
            dry_run=bool(args.dry_run),
        )
    else:
        if not args.source_file:
            raise RuntimeError("Provide either --source-api or --source-file")
        source_file = Path(args.source_file).expanduser().resolve()
        if not source_file.exists():
            raise FileNotFoundError(f"Source file not found: {source_file}")
        summary = run_import(
            source_file=source_file,
            source_name=source_name,
            delimiter=str(args.delimiter or ","),
            dry_run=bool(args.dry_run),
        )
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
