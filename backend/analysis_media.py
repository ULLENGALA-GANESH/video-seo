"""Bounded FFmpeg decoding and sequential, timestamped speech recognition."""

import json
import os
import re
import subprocess
import sys
import wave
from array import array
from math import isqrt
from pathlib import Path
from tempfile import TemporaryDirectory

import imageio_ffmpeg
import speech_recognition as sr
from speech_recognition.recognizers import google


MAX_SECONDS = 15 * 60
SAMPLE_RATE = 16_000
CHUNK_SECONDS = 30
SUPPORTED_MEDIA = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".mp3", ".wav", ".m4a", ".ogg"}
FORMATS = "mov,matroska,webm,mp3,wav,ogg"
GOOGLE_SPEECH_ENDPOINT = "https://www.google.com/speech-api/v2/recognize"


class AnalysisError(Exception):
    """An actionable error suitable for the report owner."""


def media_root():
    configured = os.getenv("MEDIA_ROOT")
    return Path(configured).resolve() if configured else Path(__file__).resolve().parent / "data" / "media"


def decode_audio(source, target):
    executable = os.getenv("FFMPEG_BINARY") or imageio_ffmpeg.get_ffmpeg_exe()
    args = [
        executable, "-nostdin", "-hide_banner", "-nostats", "-protocol_whitelist", "file,pipe",
        "-format_whitelist", FORMATS, "-threads", "2", "-i", str(source),
        "-map", "0:a:0", "-vn", "-t", str(MAX_SECONDS + 1), "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-c:a", "pcm_s16le", "-f", "wav", "-y", str(target),
    ]
    try:
        conversion = subprocess.run(args, capture_output=True, timeout=150, check=False)
    except subprocess.TimeoutExpired as exc:
        raise AnalysisError("Decoding timed out. Try a shorter clip or paste the transcript.") from exc
    if conversion.returncode:
        raise AnalysisError("Could not read an audio track. Try a supported file with audible speech.")
    # Check container duration as well as decoded audio. A long video may have a short audio track.
    match = re.search(rb"Duration:\s*(\d+):(\d+):([\d.]+)", conversion.stderr)
    if match and int(match[1]) * 3600 + int(match[2]) * 60 + float(match[3]) > MAX_SECONDS:
        raise AnalysisError("This file exceeds the 15-minute limit. Trim it and submit it again.")


def parse_google_results(response):
    """Keep final utterances by provider result index, selecting one hypothesis each."""
    results = {}
    next_index = 0
    try:
        for line in response.splitlines():
            if not line.strip():
                continue
            packet = json.loads(line)
            offset = packet.get("result_index", next_index)
            for index, result in enumerate(packet.get("result", []), offset):
                if result.get("final") is False:
                    continue
                options = [item for item in result.get("alternative", []) if item.get("transcript", "").strip()]
                if options:
                    best = max(options, key=lambda item: item.get("confidence", -1))
                    results[index] = best["transcript"].strip()
                    next_index = max(next_index, index + 1)
    except (ValueError, TypeError, AttributeError) as exc:
        raise sr.RequestError("Unexpected speech provider response.") from exc
    if not results:
        raise sr.UnknownValueError()
    return " ".join(results[i] for i in sorted(results))


def recognize_chunk(audio):
    # SpeechRecognition's stock parser discards all but the first result, including
    # in show_all mode. Reuse its request and FLAC handling with a multi-result parser.
    builder = google.create_request_builder(
        endpoint=GOOGLE_SPEECH_ENDPOINT, key=os.getenv("GOOGLE_SPEECH_KEY") or None,
        language="en-US", filter_level=0,
    )
    return parse_google_results(google.obtain_transcription(builder.build(audio), timeout=20))


def pause_boundary(pcm, rate):
    """Split at a clear utterance break, a brief gap near ten seconds, or the window end."""
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    length = len(samples)
    if length <= 4 * rate:
        return length
    block = max(1, rate // 40)  # 25 ms energy windows
    levels = []
    for start in range(0, length, block):
        values = samples[start:start + block:4]
        levels.append(isqrt(sum(value * value for value in values) // len(values)))
    ceiling = sorted(levels)[int(len(levels) * 0.8)]
    silence = max(35, min(500, ceiling // 9))
    gaps = []
    utterance_gaps = []
    quiet_start = None
    for index, level in enumerate(levels):
        if level <= silence:
            if quiet_start is None:
                quiet_start = index
        elif quiet_start is not None:
            if quiet_start > 0 and index - quiet_start >= 7:
                frame = (quiet_start + index) * block // 2
                if length - frame >= rate:
                    if index - quiet_start >= 32 and frame >= 2 * rate:
                        # The Google recognizer can drop a whole utterance across a long pause.
                        utterance_gaps.append(frame)
                    elif frame >= 6 * rate:
                        gaps.append(frame)
            quiet_start = None
    if utterance_gaps:
        earliest = utterance_gaps[0]
        if earliest <= 12 * rate or not gaps:
            return earliest
        gaps.append(earliest)
    if length <= 12 * rate:
        return length
    return min(gaps, key=lambda frame: abs(frame - 10 * rate)) if gaps else length


def recognize_audio(audio_file, progress, recognize=None):
    recognize = recognize or recognize_chunk
    segments = []
    unrecognized = []
    total_chunks = 0
    with wave.open(str(audio_file), "rb") as audio:
        rate, width, frames = audio.getframerate(), audio.getsampwidth(), audio.getnframes()
        duration = frames / rate
        if duration > MAX_SECONDS:
            raise AnalysisError("This file exceeds the 15-minute limit. Trim it and submit it again.")
        if not frames:
            raise AnalysisError("This audio track is empty.")
        if width != 2 or audio.getnchannels() != 1:
            raise AnalysisError("The decoded audio track must be 16-bit mono.")
        frame = 0
        while frame < frames:
            progress("Transcribing audio", 15 + round(68 * frame / frames))
            pcm = audio.readframes(CHUNK_SECONDS * rate)
            if not pcm:
                raise AnalysisError("The audio ended unexpectedly. Try exporting the file again.")
            count = pause_boundary(pcm, rate)
            next_frame = frame + count
            # Inspect at most 30 seconds ahead. The next read starts exactly where
            # the submitted chunk ended, including every frame on both sides of a pause.
            audio.setpos(next_frame)
            interval = {"start_seconds": round(frame / rate, 3), "end_seconds": round(next_frame / rate, 3)}
            total_chunks += 1
            try:
                text = recognize(sr.AudioData(pcm[:count * width], rate, width)).strip()
            except sr.UnknownValueError:
                text = ""
            except (sr.RequestError, TimeoutError, OSError) as exc:
                raise AnalysisError("The speech service is unavailable. Retry or paste a transcript.") from exc
            if text:
                segments.append({"text": text, **interval})
            else:
                unrecognized.append(interval)
            frame = next_frame
    if not segments:
        raise AnalysisError("No English speech was recognized. Try clearer audio or paste a transcript.")
    coverage = {
        "partial": bool(unrecognized), "total_chunks": total_chunks,
        "recognized_chunks": len(segments), "unrecognized_ranges": unrecognized,
    }
    return segments, duration, coverage


def transcribe_media(source, progress):
    if not source.is_file():
        raise AnalysisError("The original media is unavailable. Upload the file as a new analysis.")
    progress("Decoding audio", 8)
    with TemporaryDirectory(prefix="decode-", dir=source.parent) as work:
        wav = Path(work) / "speech.wav"
        decode_audio(source, wav)
        return recognize_audio(wav, progress)
