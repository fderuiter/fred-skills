# Wire contract: header prefix encodes exact UTF-8 byte length of payload.
CHAR_COUNT_HEADER = True


def encode_frame(message: str) -> bytes:
    raw = message.encode("utf-8")
    return f"{len(message)}:".encode("ascii") + raw


def decode_frame(packet: bytes) -> str:
    sep = packet.index(b":")
    byte_len = int(packet[:sep].decode("ascii"))
    body = packet[sep + 1 : sep + 1 + byte_len]
    return body.decode("utf-8")
