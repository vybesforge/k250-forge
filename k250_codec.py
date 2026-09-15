"""K250-4S wire codec + constants.

Reverse-engineered from the konnector.com web app (public JS).
"""

ADDR = "AA:BB:CC:11:22:33"          # random static LE address
NAME = "Kx250-4S"                   # advertised name
SVC = "086e0000-7935-0d3a-ca91-bfb0c8c34043"
CHR = "086e0001-7935-0d3a-ca91-bfb0c8c34043"

# what the app sends to sync/read all state (all values empty)
READ_ALL = {"AC": "", "PW": "", "MA": "", "GP": "", "PA": "", "CA": "",
            "MP": "", "SB": "", "BC": "", "CS": "", "FV": "", "ER": "0"}

# DFU commands
CMD_START, CMD_SIZE, CMD_VERSION, CMD_BLOCK, CMD_FINISH = 240, 241, 242, 32, 33


def rle_encode(data: bytes, framed: bool = True) -> bytes:
    """Zero-run-length codec used by the box (port of the app's `z()`)."""
    out = bytearray([0])
    i, n = 0, 1
    if framed:
        out.append(0)
        i += 1

    def close(x=True):
        nonlocal i, n
        out[i] = n
        i = len(out)
        if x is not False:
            out.append(0)
        n = 1

    for b in data:
        if b == 0:
            close()
        else:
            out.append(b)
            n += 1
            if n == 255:
                close()
    close(False)
    if framed:
        out.append(0)
    return bytes(out)


def rle_decode(data: bytes) -> bytes:
    """Inverse of rle_encode (port of the app's notification decoder)."""
    out = bytearray()
    o = 0
    while o < len(data):
        c = data[o]
        o += 1
        for _ in range(1, c):
            if o < len(data):
                out.append(data[o])
                o += 1
        if c < 255 and o < len(data):
            out.append(0)
    return bytes(out)


def frame(cmd: int, seq: int, payload: bytes = b"") -> bytes:
    body = (cmd.to_bytes(2, "little") + seq.to_bytes(2, "little")
            + len(payload).to_bytes(2, "little") + payload)
    return rle_encode(body, framed=True)


def ack_seq(notif: bytes) -> int:
    """Device echoes the sequence number at u16LE offset 3 of the decoded frame."""
    d = rle_decode(notif)
    return int.from_bytes(d[3:5], "little") if len(d) >= 5 else -1


if __name__ == "__main__":
    # self-test against the hand-traced reference vector
    ref = bytes.fromhex("00 02 F0 02 01 01 01 01 00".replace(" ", ""))
    got = frame(240, 1, b"")
    print("frame(240,1,'') =", got.hex())
    print("reference       =", ref.hex())
    print("MATCH" if got == ref else "MISMATCH")
