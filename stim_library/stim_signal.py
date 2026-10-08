#!/usr/bin/env python3
"""Imported stim signal — B0 frame encoder/decoder (Coyote V3 / Civet).

Reproduces the exact packets the source Web-BLE client writes every 100 ms.
Pure stdlib. See SIGNAL-FORMAT.md.
"""
import json, math, sys, os

HERE = os.path.dirname(os.path.abspath(__file__))

def get_hex(v):
    """getHexValue(): int -> 2-char uppercase hex, zero-padded (values 0..255)."""
    return f"{int(v) & 0xFF:02X}"

def base_stim(str_a, str_b):
    """baseStim = 'B0' + '0F' + hex(strA) + hex(strB)  (binaryToHex('1111') == 0x0F)."""
    return "B0" + "0F" + get_hex(str_a) + get_hex(str_b)

def build_frame(str_a, str_b, freq_a4, int_a4, freq_b4, int_b4):
    """One 20-byte B0 packet: [B0 0F sA sB][fA*4 iA*4][fB*4 iB*4]."""
    assert len(freq_a4) == len(int_a4) == len(freq_b4) == len(int_b4) == 4, "need 4-tuples"
    hx = lambda xs: "".join(get_hex(x) for x in xs)
    return base_stim(str_a, str_b) + hx(freq_a4) + hx(int_a4) + hx(freq_b4) + hx(int_b4)

def decode_frame(hexstr):
    b = bytes.fromhex(hexstr)
    assert b[0] == 0xB0, "not a B0 frame"
    return dict(cmd=b[0], flags=b[1], strA=b[2], strB=b[3],
                freqA=list(b[4:8]), intA=list(b[8:12]),
                freqB=list(b[12:16]), intB=list(b[16:20]))

def _lerp(keyframes, n, effect_freq=1.0):
    """getFrequencyTuples-style: interpolate {toVal,atTime} over n sub-values."""
    kfs = sorted(({'v': float(k['toVal']) if not isinstance(k['toVal'], list) else None,
                   'arr': k['toVal'] if isinstance(k['toVal'], list) else None,
                   't': float(k.get('atTime', 0))} for k in keyframes), key=lambda k: k['t'])
    out = []
    for i in range(n):
        t = 0.025 * i * effect_freq
        # pre-baked array keyframe: sample it
        for k in kfs:
            if k['arr'] is not None and k['t'] <= t:
                arr = k['arr']
                out.append(arr[min(int((t - k['t']) / 0.025), len(arr) - 1)])
                break
        else:
            if t <= kfs[0]['t']: out.append(kfs[0]['v'] if kfs[0]['v'] is not None else 0)
            elif t >= kfs[-1]['t']: out.append(kfs[-1]['v'] if kfs[-1]['v'] is not None else 0)
            else:
                for a, b in zip(kfs, kfs[1:]):
                    if a['t'] <= t <= b['t'] and a['v'] is not None and b['v'] is not None:
                        f = (t - a['t']) / (b['t'] - a['t']) if b['t'] > a['t'] else 0
                        out.append(a['v'] + (b['v'] - a['v']) * f)
                        break
                else:
                    out.append(0)
    return out

def tuples(vals):
    """Group a value stream every 4 -> list of 4-tuples (getTuples)."""
    return [vals[i:i+4] for i in range(0, len(vals) - len(vals) % 4, 4)]

def frames_from_stim(entry, str_a=100, str_b=100, ticks=6):
    """Render the first `ticks` B0 packets for a dictionary entry (mono -> both ch)."""
    freq = entry['frequence'][0] if isinstance(entry.get('frequence'), list) else entry.get('frequence', 1000)
    ef = entry.get('effectFrequence', [1])
    ef = ef[0] if isinstance(ef, list) else ef
    vol = entry['volume'][0] if isinstance(entry.get('volume'), list) else entry.get('volume', -20)
    # Map dB volume ~ intensity%; frequency stays the carrier. (Keyword lanes; tune to taste.)
    inten = max(0, min(100, 100 + vol * 2))  # -20dB -> 60%, -30dB -> 40%
    packets = []
    for t in range(ticks):
        f = [int(freq % 200)] * 4           # carrier (K250 MA band)
        a = [int(inten)] * 4
        packets.append(build_frame(str_a, str_b, f, a, f, a))
    return packets

if __name__ == "__main__":
    # 1) round-trip a synthetic frame
    fr = build_frame(60, 60, [100,150,200,250], [40,45,50,55], [100,150,200,250], [40,45,50,55])
    print("synthetic frame (%d bytes / %d hex):" % (len(fr)//2, len(fr)))
    print("  ", fr)
    print("   decoded:", decode_frame(fr))
    print()
    # 2) render a real catalog stim
    cat = json.load(open(os.path.join(HERE, "dictionnary_estims.json")))
    for e in cat['edge']:
        if isinstance(e, dict) and e.get('sequence'):
            print("stim:", e['name'], " freq=", e.get('frequence'), " vol=", e.get('volume'))
            for i, p in enumerate(frames_from_stim(e, ticks=4)):
                d = decode_frame(p)
                print(f"  t={i*100:>4}ms  {p}  A(f={d['freqA']} i={d['intA']})")
            break
