import io
import wave

#: What Gemini's TTS models return (`audio/L16;codec=pcm;rate=24000`), and
#: therefore what the stub produces too -- one sample rate through the whole
#: system means nothing downstream has to resample.
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1

WAV_MIME = "audio/wav"


def wrap_pcm16(pcm: bytes, *, rate: int = SAMPLE_RATE, channels: int = CHANNELS) -> bytes:
    """Put a RIFF header on raw little-endian 16-bit PCM.

    Gemini's TTS endpoint answers with headerless PCM, which no browser and
    no media library will play, so something has to write the 44 bytes in
    front of it. stdlib `wave` rather than packing the header by hand: it
    is the same amount of code, and it computes the chunk sizes, which is
    the part a hand-rolled version gets wrong when the payload is odd-length.
    """
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(channels)
        out.setsampwidth(SAMPLE_WIDTH)
        out.setframerate(rate)
        out.writeframes(pcm)
    return buffer.getvalue()
