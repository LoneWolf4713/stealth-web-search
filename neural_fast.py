"""
Fast Neural Acceleration Module (CUDA / CPU auto-sensing).
- GLiNER: Zero-shot arbitrary entity extraction with case-insensitive deduplication and confidence thresholding.
- Cross-Encoder: Calibrated semantic reranker with normalized percentage match scores.
- Automatic hardware fallback: CUDA if GPU is enabled, otherwise multicore CPU.
"""
import os
import sys
import math
import logging
import warnings
from typing import List, Dict, Any, Optional, Tuple

# Suppress progress bars and library warnings for clean CLI/MCP output
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
logging.getLogger("transformers").setLevel(logging.ERROR)
logging.getLogger("huggingface_hub").setLevel(logging.ERROR)
logging.getLogger("gliner").setLevel(logging.ERROR)

logger = logging.getLogger("neural_fast")

_GLINER_MODEL = None
_RERANKER_MODEL = None
_RERANKER_TOKENIZER = None

def get_device() -> str:
    """Detect CUDA availability; fallback to CPU if GPU is disabled/unavailable."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"

def sigmoid(val: float) -> float:
    """Standard logistic sigmoid function."""
    try:
        return 1.0 / (1.0 + math.exp(-val))
    except OverflowError:
        return 0.0 if val < 0 else 1.0

# --- 1. GLiNER: Zero-Shot Entity Extractor ---
def load_gliner(model_name: str = "urchade/gliner_small-v2.1"):
    global _GLINER_MODEL
    if _GLINER_MODEL is not None:
        return _GLINER_MODEL
    try:
        from gliner import GLiNER
        device = get_device()
        _GLINER_MODEL = GLiNER.from_pretrained(model_name)
        if device == "cuda":
            _GLINER_MODEL = _GLINER_MODEL.to("cuda")
        return _GLINER_MODEL
    except Exception as e:
        logger.warning(f"Could not load GLiNER: {e}")
        return None

def extract_entities(
    text: str,
    labels: Optional[List[str]] = None,
    threshold: float = 0.55
) -> List[Dict[str, Any]]:
    """
    Extracts arbitrary entity types with case-insensitive deduplication and confidence cutoff.
    Avoids extracting noisy boilerplate or repeated tokens.
    """
    if not text or len(text.strip()) < 50:
        return []

    if not labels:
        labels = [
            "technology", "framework", "library", "tool",
            "hackathon", "grant", "fellowship", "competition",
            "company", "organization", "deadline", "prize"
        ]

    model = load_gliner()
    if model is None:
        return []

    try:
        truncated_text = text[:3500]
        entities = model.predict_entities(truncated_text, labels, threshold=threshold)
        
        # Sort by confidence score descending and deduplicate case-insensitively
        sorted_raw = sorted(entities, key=lambda x: x.get("score", 0), reverse=True)
        seen = set()
        unique_entities = []

        for ent in sorted_raw:
            clean_name = ent.get("text", "").strip()
            norm = clean_name.lower()
            if len(clean_name) < 2 or norm in seen:
                continue
            # Filter out numbers-only or common false positives
            if norm in ("javascript", "browser", "cookies", "please enable", "click here", "read more"):
                continue
            seen.add(norm)
            unique_entities.append({
                "entity": clean_name,
                "label": ent.get("label", "entity"),
                "score": round(float(ent.get("score", 0)), 2)
            })
            if len(unique_entities) >= 6:
                break

        return unique_entities
    except Exception as e:
        logger.warning(f"GLiNER inference error: {e}")
        return []

# --- 2. Cross-Encoder: Fast Semantic Reranker ---
def load_reranker(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
    global _RERANKER_MODEL, _RERANKER_TOKENIZER
    if _RERANKER_MODEL is not None:
        return _RERANKER_MODEL, _RERANKER_TOKENIZER
    try:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        device = get_device()
        tokenizer = AutoTokenizer.from_pretrained(model_name)
        model = AutoModelForSequenceClassification.from_pretrained(model_name)
        model.eval()
        if device == "cuda":
            model = model.to("cuda")
        _RERANKER_MODEL = model
        _RERANKER_TOKENIZER = tokenizer
        return _RERANKER_MODEL, _RERANKER_TOKENIZER
    except Exception as e:
        logger.warning(f"Could not load Reranker: {e}")
        return None, None

def rerank_chunks(
    query: str,
    chunks: List[Dict[str, Any]],
    instruction: Optional[str] = None,
    top_k: int = 3
) -> List[Dict[str, Any]]:
    """
    Joint cross-attention scoring between (Query + Instruction) and Document chunks.
    Deduplicates identical chunks, normalizes scores to human/LLM-readable percentages,
    and caps individual passage lengths.
    """
    if not chunks:
        return []

    # Deduplicate chunks based on normalized leading text
    seen_texts = set()
    deduped_chunks = []
    for c in chunks:
        content = c.get("content", "").strip()
        key = content[:150].lower()
        if key in seen_texts:
            continue
        seen_texts.add(key)
        deduped_chunks.append(c)

    model, tokenizer = load_reranker()
    if model is None or tokenizer is None:
        for c in deduped_chunks[:top_k]:
            c["match_pct"] = "N/A"
            if len(c["content"]) > 1500:
                c["content"] = c["content"][:1450] + "\n[... Passage truncated ...]"
        return deduped_chunks[:top_k]

    import torch
    device = get_device()

    full_query = f"{instruction} Query: {query}" if instruction else query
    pairs = [(full_query, c.get("content", "")) for c in deduped_chunks]

    try:
        features = tokenizer(
            pairs,
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt"
        )
        if device == "cuda":
            features = {k: v.to("cuda") for k, v in features.items()}

        with torch.no_grad():
            scores = model(**features).logits.squeeze().cpu().tolist()

        if isinstance(scores, float):
            scores = [scores]

        # Attach raw score and calibrated percentage match
        for idx, score in enumerate(scores):
            raw_val = float(score)
            deduped_chunks[idx]["relevance_score"] = raw_val
            pct = int(round(sigmoid(raw_val) * 100))
            deduped_chunks[idx]["match_pct"] = f"{pct}% Match"

        # Sort descending by raw score
        sorted_chunks = sorted(deduped_chunks, key=lambda x: x.get("relevance_score", 0), reverse=True)
        
        # Filter out low-relevance noise:
        # Keep passages with >= 12% match. If no passages meet 12%, keep only the single top passage as fallback.
        top_chunks = []
        for idx, c in enumerate(sorted_chunks[:top_k]):
            pct = int(round(sigmoid(c.get("relevance_score", -99)) * 100))
            if pct >= 12:
                top_chunks.append(c)
            elif idx == 0:
                top_chunks.append(c)

        # Per-passage length safety cap (never exceed 1,500 characters per passage)
        for c in top_chunks:
            if len(c["content"]) > 1500:
                c["content"] = c["content"][:1450] + "\n[... Passage truncated ...]"

        return top_chunks
    except Exception as e:
        logger.warning(f"Reranking error: {e}")
        for c in deduped_chunks[:top_k]:
            c["match_pct"] = "N/A"
            if len(c["content"]) > 1500:
                c["content"] = c["content"][:1450] + "\n[... Passage truncated ...]"
        return deduped_chunks[:top_k]

if __name__ == "__main__":
    dev = get_device()
    print(f"Neural Acceleration Initialized. Device: {dev.upper()}")
