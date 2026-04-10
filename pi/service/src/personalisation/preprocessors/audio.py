import io
from typing import Any


def preprocess(content: bytes, filename: str) -> dict[str, Any]:
    """
    Analyse audio and return an embeddable description + feature metadata.
    Requires: librosa, soundfile
    """
    try:
        import numpy as np
        import librosa

        y, sr = librosa.load(io.BytesIO(content), sr=None, mono=True)

        # BPM
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        bpm = float(round(float(tempo), 1))

        # Key centre via chroma
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        key_idx = int(np.argmax(chroma.mean(axis=1)))
        note_names = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        estimated_key = note_names[key_idx]

        # Spectral + energy
        spectral_centroid = float(librosa.feature.spectral_centroid(y=y, sr=sr).mean())
        zcr = float(librosa.feature.zero_crossing_rate(y).mean())
        rms = float(librosa.feature.rms(y=y).mean())
        duration = float(librosa.get_duration(y=y, sr=sr))

        features = {
            "bpm": bpm,
            "estimated_key": estimated_key,
            "duration_seconds": round(duration, 2),
            "spectral_centroid_hz": round(spectral_centroid, 2),
            "zero_crossing_rate": round(zcr, 4),
            "rms_energy": round(rms, 4),
        }

        description = (
            f"Audio '{filename}': {round(duration)}s, ~{bpm} BPM, "
            f"key centre {estimated_key}, spectral centroid {round(spectral_centroid)}Hz"
        )

    except ImportError:
        features = {}
        description = f"Audio file '{filename}' (install librosa for analysis)"
    except Exception as e:
        features = {}
        description = f"Audio file '{filename}' (analysis failed: {e})"

    return {
        "text": description,
        "metadata": {"filename": filename, "media_type": "audio", **features},
    }
