import os
import tempfile
from typing import Any


def preprocess(content: bytes, filename: str) -> dict[str, Any]:
    """
    Extract metadata from a video file.
    Full analysis requires ffmpeg-python: pip install personalisation-service[video]
    """
    description = f"Video file '{filename}'"
    metadata: dict[str, Any] = {"filename": filename, "media_type": "video"}

    try:
        import ffmpeg

        ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else ".mp4"
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        try:
            probe = ffmpeg.probe(tmp_path)
            fmt = probe.get("format", {})
            streams = probe.get("streams", [])

            video_streams = [s for s in streams if s.get("codec_type") == "video"]
            audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
            duration = round(float(fmt.get("duration", 0)), 2)

            if video_streams:
                vs = video_streams[0]
                width = vs.get("width", "?")
                height = vs.get("height", "?")
                fps_raw = vs.get("r_frame_rate", "0/1")
                num, den = (int(x) for x in fps_raw.split("/")) if "/" in fps_raw else (0, 1)
                fps = round(num / den, 1) if den else 0

                metadata.update({
                    "width": width,
                    "height": height,
                    "fps": fps,
                    "duration_seconds": duration,
                    "has_audio": len(audio_streams) > 0,
                })

                description = (
                    f"Video '{filename}': {width}x{height}, {fps}fps, {duration}s"
                    + (", has audio" if audio_streams else "")
                )
        finally:
            os.unlink(tmp_path)

    except ImportError:
        description = (
            f"Video file '{filename}' — install ffmpeg-python for full analysis: "
            "pip install 'personalisation-service[video]'"
        )
    except Exception as e:
        description = f"Video file '{filename}' (analysis failed: {e})"

    return {"text": description, "metadata": metadata}
