import hashlib
import json
import math
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Protocol

MODEL_REPO = "Qdrant/paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
MODEL_REVISION = "faf4aa4225822f3bc6376869cb1164e8e3feedd0"
MODEL_FILES = ("model_optimized.onnx", "tokenizer.json", "config.json", "tokenizer_config.json")
DIMENSION = 384
TOKEN_LIMIT = 128


class EmbeddingUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TextChunk:
    text: str
    start: int
    end: int


class Embedder(Protocol):
    signature: str
    dimension: int

    def chunks(self, text: str) -> tuple[list[TextChunk], bool]: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


def validate_vectors(vectors: list[list[float]], count: int, dimension: int) -> None:
    if len(vectors) != count:
        raise ValueError("Embedding response count mismatch")
    for vector in vectors:
        if len(vector) != dimension or not all(math.isfinite(value) for value in vector):
            raise ValueError("Invalid embedding dimension or non-finite value")
        if sum(value * value for value in vector) <= 0:
            raise ValueError("Embedding must have non-zero norm")


def download_model(directory: Path) -> dict[str, str]:
    from huggingface_hub import snapshot_download

    # Downloads only model assets; no job or query text is sent to Hugging Face.
    snapshot_download(
        repo_id=MODEL_REPO,
        revision=MODEL_REVISION,
        allow_patterns=list(MODEL_FILES),
        local_dir=directory,
        token=False,
    )
    hashes = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in MODEL_FILES
    }
    manifest = {"repo": MODEL_REPO, "revision": MODEL_REVISION, "sha256": hashes}
    (directory / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {"repo": MODEL_REPO, "revision": MODEL_REVISION, "directory": str(directory)}


class LocalMiniLM:
    """Pinned quantized ONNX MiniLM; CPU-only, local assets, mean pooling + L2 norm."""

    dimension = DIMENSION

    def __init__(self, directory: Path, *, threads: int = 2, max_chunks: int = 64):
        self.directory = directory
        self.threads = threads
        self.max_chunks = max_chunks
        self._lock = threading.RLock()
        self._loaded = False
        # Runtime versions are part of the index identity as well as the pinned weights.
        identity = f"{MODEL_REPO}@{MODEL_REVISION}:mean-l2-128:chunks96overlap24:{max_chunks}:v1"
        identity += ":" + ":".join(
            version(package) for package in ("onnxruntime", "tokenizers", "numpy")
        )
        self.signature = hashlib.sha256(identity.encode()).hexdigest()

    def _load(self) -> None:
        if self._loaded:
            return
        try:
            from onnxruntime import InferenceSession, SessionOptions, disable_telemetry_events
            from tokenizers import Tokenizer

            disable_telemetry_events()
            manifest = json.loads((self.directory / "manifest.json").read_text(encoding="utf-8"))
            if manifest["revision"] != MODEL_REVISION or manifest["repo"] != MODEL_REPO:
                raise ValueError("Wrong model manifest")
            for name in MODEL_FILES:
                digest = hashlib.sha256((self.directory / name).read_bytes()).hexdigest()
                if digest != manifest["sha256"][name]:
                    raise ValueError("Model artifact checksum mismatch")
            self.tokenizer = Tokenizer.from_file(str(self.directory / "tokenizer.json"))
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()
            options = SessionOptions()
            options.intra_op_num_threads = self.threads
            options.inter_op_num_threads = 1
            self.session = InferenceSession(
                str(self.directory / "model_optimized.onnx"),
                sess_options=options,
                providers=["CPUExecutionProvider"],
            )
            self._loaded = True
        except Exception as exc:
            raise EmbeddingUnavailable(
                "Local embedding assets unavailable; run download-model"
            ) from exc

    def chunks(self, text: str) -> tuple[list[TextChunk], bool]:
        with self._lock:
            self._load()
            self.tokenizer.no_truncation()
            self.tokenizer.no_padding()
            encoded = self.tokenizer.encode(text, add_special_tokens=False)
            offsets = encoded.offsets
            result: list[TextChunk] = []
            for start in range(0, len(offsets), 72):
                end = min(start + 96, len(offsets))
                if len(result) >= self.max_chunks:
                    return result, True
                begin_char, end_char = offsets[start][0], offsets[end - 1][1]
                result.append(TextChunk(text[begin_char:end_char], begin_char, end_char))
                if end == len(offsets):
                    break
            return result or [TextChunk(text, 0, len(text))], False

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        import numpy as np

        with self._lock:
            self._load()
            self.tokenizer.enable_truncation(max_length=TOKEN_LIMIT)
            self.tokenizer.enable_padding()
            result: list[list[float]] = []
            inputs = {value.name for value in self.session.get_inputs()}
            for start in range(0, len(texts), 8):
                batch = self.tokenizer.encode_batch(list(texts[start : start + 8]))
                feed = {
                    "input_ids": np.asarray([item.ids for item in batch], dtype=np.int64),
                    "attention_mask": np.asarray(
                        [item.attention_mask for item in batch], dtype=np.int64
                    ),
                    "token_type_ids": np.asarray([item.type_ids for item in batch], dtype=np.int64),
                }
                output = self.session.run(
                    None, {key: value for key, value in feed.items() if key in inputs}
                )[0]
                if output.ndim != 3 or output.shape[-1] != self.dimension:
                    raise ValueError("Unexpected ONNX token embedding output")
                mask = feed["attention_mask"][:, :, None].astype(np.float32)
                pooled = (output * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
                pooled /= np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
                result.extend(pooled.astype(float).tolist())
            validate_vectors(result, len(texts), self.dimension)
            return result
