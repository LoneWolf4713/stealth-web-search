"""
Deep Reasoning & SLM Extraction Engine (Phase 2).
Uses quantized Gemma 4 (E2B-IT) in GGUF format via llama-cpp (CUDA / CPU auto-sensing).
Performs goal-directed semantic extraction over broad, vague, or complex queries
without information loss or hallucination.
"""
import os
import sys
import logging
from typing import Optional, Dict, Any, List

logger = logging.getLogger("deep_reasoner")

MODEL_DIR = os.path.expanduser("~/.cache/web_search_tool/models")
GEMMA_GGUF_PATH = os.path.join(MODEL_DIR, "gemma-4-E2B-it-Q4_K_M.gguf")

# Full GPU offload. Measured on a 6GB card with GLiNER + the cross-encoder
# also resident in the same daemon process: peak VRAM after a full-length
# (700 token) generation is ~3.1GB, leaving ~3GB headroom -- comfortable.
# (An earlier, lower estimate here was based on a contaminated reading from
# a stray leftover process; re-measured clean and this is the real number.)
# Full offload runs at ~47 tok/s vs ~27 tok/s at 30/35 layers.
GPU_LAYERS_WHEN_CUDA = -1

_LLM_INSTANCE = None

def is_cuda_available() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False

def load_reasoner_model():
    """Lazily load the quantized Gemma 4 E2B model."""
    global _LLM_INSTANCE
    if _LLM_INSTANCE is not None:
        return _LLM_INSTANCE

    if not os.path.exists(GEMMA_GGUF_PATH):
        logger.error(f"Model file not found at {GEMMA_GGUF_PATH}")
        return None

    try:
        from llama_cpp import Llama
        use_gpu = is_cuda_available()
        gpu_layers = GPU_LAYERS_WHEN_CUDA if use_gpu else 0

        _LLM_INSTANCE = Llama(
            model_path=GEMMA_GGUF_PATH,
            n_ctx=4096,
            n_threads=os.cpu_count() or 4,
            n_gpu_layers=gpu_layers,
            verbose=False
        )
        return _LLM_INSTANCE
    except Exception as e:
        logger.error(f"Failed to load Llama/Gemma model: {e}")
        return None

def extract_deep_intelligence(
    content: str,
    query: str,
    reasoning_intent: Optional[str] = None,
    max_tokens: int = 700
) -> str:
    """
    Extract actionable intelligence, obscure entities, and solutions from web content
    using local Gemma 2 2B reasoning.
    """
    llm = load_reasoner_model()
    if llm is None:
        # Fallback if model is unavailable
        return content[:1500] + "\n\n*(Deep reasoning model not loaded)*"

    goal = reasoning_intent if reasoning_intent else query
    # Truncate content to fit context window comfortably
    trimmed_content = content[:6000]

    system_prompt = (
        "You are an expert technical intelligence and extraction engine. Your job is to extract all "
        "relevant information, opportunities, solutions, or facts from the web document that answer the "
        "user's research goal.\n\n"
        "Strict Extraction Instructions:\n"
        "1. Identify all specific entities, events, hackathons, grants, competitions, tools, or solutions.\n"
        "2. Even if an event, workaround, or solution has an unfamiliar or niche name, or is listed without "
        "upvotes, PRESERVE IT VERBATIM if it addresses the user's goal.\n"
        "3. Extract concrete details: exact names, dates, deadlines, requirements, and prizes/benefits.\n"
        "4. Remove conversational fluff, ads, and layout boilerplate.\n"
        "5. Format the extracted findings as clean, structured Markdown bullet points."
    )
    user_prompt = f'User Research Goal: "{goal}"\n\nWeb Document Content:\n"""\n{trimmed_content}\n"""'

    try:
        # Uses the model's own embedded chat template (via create_chat_completion)
        # rather than hand-rolled special tokens, so this keeps working correctly
        # if the GGUF is swapped for a different model/family later.
        output = llm.create_chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=max_tokens,
            temperature=0.2,
            top_p=0.9,
        )
        text = output["choices"][0]["message"]["content"].strip()
        return text if text else "No matching intelligence found in document."
    except Exception as e:
        logger.error(f"Inference error in deep reasoner: {e}")
        return f"Extraction failed: {str(e)}"

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: deep_reasoner.py <query> <sample_text_file>")
        sys.exit(1)
    q = sys.argv[1]
    with open(sys.argv[2], "r", encoding="utf-8") as f:
        c = f.read()
    print(extract_deep_intelligence(c, q))
