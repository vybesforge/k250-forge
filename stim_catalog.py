#!/usr/bin/env python3
"""One merged stim catalogue: the engine's own patterns + the imported stims,
sorted into the SAME categories, de-duplicated, with readable names.

The engine's patterns were themselves translated from the same HOWL-family wave
vocabulary, so `estimAIPool` in the imported dictionary is *literally* the source
of pattern names like tickles / light_pulse / edging_wave — those are the
duplicates and are dropped. This module is the single grouping the page renders.

Belt and braces on duplicates: a name that appears twice is dropped, and so is any
stim whose *playable timeline* is identical to one already kept — so two differently
named clones of the same signal collapse to one entry, no matter what they are called.
"""
import hashlib
import json
import os
import re

try:
    import stim_translate as _T          # for the playable-timeline duplicate check
except Exception:                        # pragma: no cover - keeps the page up if it moves
    _T = None

HERE = os.path.dirname(os.path.abspath(__file__))
CATALOG = os.path.join(HERE, "stim_library", "dictionnary_estims.json")

# --- the engine's own patterns, in their groups (was DRIVE_GROUPS in the page) ---
ENGINE_GROUPS = [
    ("Tease / gentle", [
        ("tickles", "very low continuous, the 'is it on?' base"),
        ("light_pulse", "low pulsing, gentle"),
        ("light_stutter", "low stuttering, uneven"),
        ("teasing_pulse", "medium pulsing, settle into it"),
        ("teasing_stutter", "medium stuttering, playful"),
        ("teasing_continuous", "medium steady hum"),
        ("tide", "slow swell up and down"),
        ("drift", "swell while MA drifts under it"),
        ("buzzline", "power climbs, MA wanders"),
        ("cooldown", "burst of beats then a low hum"),
    ]),
    ("Build / edge", [
        ("edge", "hover under the line, push over"),
        ("verge", "edge, but the push is a thump"),
        ("lift", "verge with power compensation"),
        ("dread", "long near-nothing, one heavy beat"),
        ("pulse", "mid MA, irregular power pulses"),
        ("ration", "2-5 beats at peak, long silence"),
        ("crescendo", "discrete escalating pulses"),
        ("edging_pulse", "high pulsing toward the edge"),
        ("edging_wave", "high wave, right at the line"),
        ("edging_continuous", "high relentless, no relief"),
        ("tease", "throttled build that never lands"),
    ]),
    ("Rhythm / beat", [
        ("groove", "locked to the thump, 2 beats/s"),
        ("metronome", "slow heavy beat, power surging"),
        ("heartbeat", "lub-dub, two surges then pause"),
        ("knead", "fast even low<->high alternation"),
        ("bounce", "power bounces, MA rises"),
        ("ripple", "power ripples, MA glides up"),
        ("rain", "quick taps then a long hold"),
    ]),
    ("Deep / heavy", [
        ("heavy", "MA=8000, power compensated hard"),
        ("compress", "power steady, MA sinks to thump"),
        ("ascend", "both axes rising together"),
        ("swell", "ascend, then it comes back down"),
        ("speed_sweep", "power flat, MA sine-sweeps"),
        ("speed_step", "power flat, MA steps up"),
        ("lie", "power falls while MA ramps up"),
        ("dual", "power steps up, MA glides up"),
        ("crawl", "power flat, MA ping-pongs 0<->2500 in 250-steps"),
        ("sweep_drop", "climb speed, then drop instantly"),
        ("sweep_hold_zero", "dwells at both ends"),
        ("arc", "composed arc of the winners"),
        ("high_sweep", "both axes slow, out of phase"),
        ("power_sweep", "speed parked, power isolated"),
        ("power_sweep_rich", "power sweep with a shape"),
        ("signature", "every element that worked"),
    ]),
    ("Penetration / oral", [
        ("thrust", "slow deep penetration stroke"),
        ("engulf", "mouth works you, takes you deep"),
        ("sound", "sounding rod, slow and deep"),
        ("milker", "slow deep pull then fast buzz"),
        ("flick", "quick tongue-flicks"),
        ("jelly", "wobbling pulse, soft shake"),
        ("fangs", "two quick bites per cycle"),
    ]),
    ("Pain / punish", [
        ("pain_stutter", "sharp uneven stutter"),
        ("pain_slow_ramp", "slow ramp to painful, holds"),
        ("pain_shocks", "single sharp shocks, long gaps"),
        ("stutter", "irregular bursts, no rhythm"),
        ("trap", "hold, drop, come back higher"),
        ("dice", "random walk with surprise zeros"),
        ("climb", "sawtooth creep up, snap back"),
        ("switchback", "climb, snap-back is a thump"),
        ("friction", "rapid granular pulses, MA climbs"),
        ("pain_edge", "pleasure band, then a scheduled excursion above it"),
    ]),
]

GROUP_ORDER = [g for g, _ in ENGINE_GROUPS]

# --- source category -> group (None = drop) ---
CAT_GROUP = {
    "generate_tease": "Tease / gentle",
    "generate_frustration": "Tease / gentle",
    "redgreenlight": "Tease / gentle",
    "EB-1-P": "Tease / gentle",          # intro / warm-up sequences
    "estimTower": "Rhythm / beat",       # audio tracks
    "gifs": "Rhythm / beat",             # gif-synced sequences
    "Jouissif": "Rhythm / beat",
    "edge": "Build / edge",
    "endOrgasm": "Build / edge",
    "TandDCFNM1": "Build / edge",        # tease & denial set
    "miscs": "Build / edge",
    "interrogatoire": "Pain / punish",
    "painPatterns": "Pain / punish",
    "estimAIPain": "Pain / punish",
    "system": None,                      # calibration is a setup task, not a scene
    # dropped:
    "estimAIPool": None,                 # == the engine's own patterns (dupes)
    "devtest": None,                     # dev tests
    "stop": None, "Trop faible": None, "Trop fort": None, "Bug": None,
    "estimAI": None,                     # routed per-name below
}

# per-name group overrides (beat the category rule)
NAME_GROUP = {
    "milkingFeelingMediumLowFreq": "Penetration / oral",
    "milkingFeelingSlowLowFreq": "Penetration / oral",
    "tripleMilkFastLowFreq": "Penetration / oral",
    "lowFreqPleasure": "Deep / heavy",
    "pleasant_staccato": "Tease / gentle",
    "pleasant_ramp": "Tease / gentle",
    "pleasant_staccato_unsync": "Tease / gentle",
    "pleasant_ramp_unsync": "Tease / gentle",
    "delightful_staccato": "Build / edge",
    "delightful_ramp": "Build / edge",
    "delightful_staccato_unsync": "Build / edge",
    "delightful_ramp_unsync": "Build / edge",
    "orgasmic": "Build / edge",
    "orgasmic_unsync": "Build / edge",
    "orgasmic_unsync_old": "Build / edge",
    "orgasmic_unsync_old2": "Build / edge",
    "continuousLight": "Tease / gentle",
    "pushTheLimitsVolumeSine": "Build / edge",
    "frustrationVolumeSine": "Build / edge",
    "LighterStrokeCaressSlow": "Tease / gentle",
    "LighterStrokeCaressSlow_unsynced": "Tease / gentle",
}

# hand-fixed display labels for the ones the normalizer mangles
LABEL_OVERRIDES = {
    "edge_2_pulse_unsynced": "Edge pulse 2 (unsynced)",
    "edge_2_pulse_ease": "Edge pulse 2 · ease",
    "edge_pulse_volume_sine_unsynced": "Edge pulse sine swell (unsynced)",
    "edge_rampPulse_pulse": "Edge ramp-pulse",
    "edge_continuous_pulse_unsynced": "Edge continuous pulse (unsynced)",
    "edge_continuous_pulse": "Edge continuous pulse",
    "edge_1_continuous": "Edge 1 · continuous",
    "endOrgasm_final": "End-orgasm · final",
    "endOrgasm_5": "End-orgasm 5", "endOrgasm_4": "End-orgasm 4",
    "endOrgasm_3": "End-orgasm 3", "endOrgasm_2": "End-orgasm 2",
    "endOrgasm_1": "End-orgasm 1",
    "reward": "Reward (green-light)",
    # the opaque "gif" set — named from what each one actually is (type / shape / freq)
    "gifankha": "Sine drift · deep",
    "gif1": "Sine hum · gentle",
    "24528781a": "Pleasure pulse · quick",
    "24854991a": "Pain pulse · low 1",
    "30272281a": "Pain pulse · low 2",
    "48562581a": "Sine wave · mid",
    "44219411a": "Square buzz · fast",
    "49835641a": "Square buzz · faster",
    "trioletOnelongMediumLowFreqSquare": "Triolet one-long · medium low-freq square",
    "longStrokeShortBreakSlow": "Long stroke, short break · slow",
    "longPulseShortPauseFastLowFreq": "Long pulse, short pause · fast low-freq",
    "longPulseShortPause": "Long pulse, short pause",
    "2RampsMediumLowFreq": "2 ramps · medium low-freq",
    "2RampsFastLowFreq": "2 ramps · fast low-freq",
    "waterfall2BMediumLowFreq": "Waterfall · 2B medium low-freq",
    "volumeSineFast": "Sine swell · fast",
    "volumeSineMedium": "Sine swell · medium",
    "volumeSineSlow": "Sine swell · slow",
    "frustrationVolumeSine": "Frustration sine swell",
    "pushTheLimitsVolumeSine": "Push the limits sine swell",
    "CustomTripleSqueezeFastLowFreq": "Custom triple squeeze · fast low-freq",
    "pulse2BMedium": "Pulse · 2B medium",
    "pulse2BSlow": "Pulse · 2B slow",
    "pulse2BFastSquare": "Pulse · 2B fast square",
    "VolumeSqueeze2BFastLowFreq": "Squeeze swell · 2B fast low-freq",
    "VolumeSqueeze2BMediumLowFreq": "Squeeze swell · 2B medium low-freq",
    "continuousPain": "Continuous pain",
    "normal": "Pain · normal",
    "wait_for_it": "Pain · wait for it",
    "up_and_down": "Pain · up and down",
    "rapid_fire": "Pain · rapid fire",
    "stutter_pain": "Pain · stutter",
    "minIntensity": "Min intensity",
    "maxVolumeCalibration": "Max amplitude calibration",
    "lightPleasureCalibration": "Light pleasure calibration",
    "2S1LSlow": "2 strong 1 light · slow 1",
    "2S1LSlow2": "2 strong 1 light · slow 2",
    "2S1LSlow4": "2 strong 1 light · slow 3",
    "miniPulseShocks": "Mini pulse shocks",
    "festivalMokkoriVolumeSineUnsync": "Festival mokkori sine swell (unsynced)",
    "longStutterMedium": "Long stutter · medium",
    "stutterRegularMedium": "Stutter regular · medium",
    "stutterRegularDualUnsync": "Stutter regular dual (unsynced)",
    "UnsyncedPulseDual": "Unsynced pulse dual",
    "UnsyncedUnregularDualPulse": "Unsynced irregular dual pulse",
    "stutterUnRegularSlow_1": "Stutter irregular · slow 1",
    "stutterUnRegularSlow_unsynced_2": "Stutter irregular · slow 2 (unsynced)",
    "stutterRegularSlow_unsynced": "Stutter regular · slow (unsynced)",
}

# when two stims turn out to be the same signal, which NAME is the better keeper
KEEP_NAMES = {
    "Intro5longPulseShortPause",       # completes the Intro 1-7 staircase
    "pleasant_staccato",               # clearer than delightful_staccato
    "pulse2BMedium",                   # clearer than FreqSqueeze2BFastLowFreq
    "continuousLight",                 # clearer than minIntensity
    "milkingFeelingSlowLowFreq",       # Penetration / oral is the sparse group
}

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

_LABEL_RULES = [
    # Intro 1-7 and the Orgasmic variants are labelled from their own numbers below.
    (re.compile(r"^(\w+?)Squeeze2B(Fast|Medium|Slow)(LowFreq)?(Square)?$"),
     lambda m: ("%s squeeze · 2B %s%s%s" % (
         m.group(1).lower(), m.group(2).lower(),
         " low-freq" if m.group(3) else "", " square" if m.group(4) else "")).strip()),
]


def _sentence(s):
    """Sentence case across the whole string, keeping tokens like 2B / GIF / 100a."""
    keep = lambda w: w.isupper() or any(c.isdigit() for c in w)
    ws = s.split()
    if not ws:
        return s
    ws = [ws[0][:1].upper() + ws[0][1:]] + [w if keep(w) else w.lower() for w in ws[1:]]
    return " ".join(ws)


def pretty(name):
    """A readable label from a machine stim name."""
    if name in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[name]
    base = name.split("/")[-1]
    if base.lower().endswith(".mp3"):
        return "Audio " + re.sub(r"\.[a-z0-9]+$", "", base)
    if re.fullmatch(r"\d+", base):
        return base
    for rx, fn in _LABEL_RULES:
        m = rx.match(base)
        if m:
            return _sentence(fn(m))
    if re.fullmatch(r"[0-9a-f]{6,}[a-z]?", base):
        return "Imported " + base
    if base.lower().startswith("gif"):
        return "Imported " + base[3:]
    s = re.sub(r"\s*_\s*", " ", base)
    s = s.replace("2B", " 2B ")
    s = _CAMEL.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    words = []
    for w in s.split():
        lw = w.lower()
        if lw in ("unsynced", "unsync"):
            words.append("(unsynced)")
        elif lw == "2b":
            words.append("2B")
        else:
            words.append(w)
    out = _sentence(" ".join(words))
    out = re.sub(r"\blow freq\b", "low-freq", out)
    out = re.sub(r"\s*\(unsyn?c(ed)?\)", " (unsynced)", out)
    return out


def _timeline(entry):
    """The stim's playable (freq, volume) timeline, or None if it cannot be read."""
    if _T is None:
        return None
    try:
        frames, _ = _T.stim_timeline(entry)
    except Exception:
        return None
    return frames or None


def _timeline_sig(entry):
    """Hash of the playable timeline — identical stims hash identically."""
    frames = _timeline(entry)
    if not frames:
        return None
    return hashlib.sha1(json.dumps([[round(f, 2), round(v, 2)]
                                    for _, f, v in frames]).encode()).hexdigest()


def _mean_hz(entry):
    frames = _timeline(entry)
    if not frames:
        return None
    return round(sum(f for _, f, _ in frames) / len(frames))


def _data_label(name, entry):
    """Labels that need the entry's own numbers — measured, so they cannot drift
    out of step with the signal they describe."""
    hz = _mean_hz(entry)
    if not hz:
        return None
    m = re.match(r"^Intro(\d)longPulseShortPause$", name)
    if m:
        return f"Intro {m.group(1)} · {hz} Hz"
    if name.startswith("orgasmic_unsync"):
        return f"Orgasmic (unsynced) · {hz} Hz"
    return None


def _engine_label(key):
    """Display name for an engine pattern: the same words, no underscores.

    The KEY is deliberately unchanged — that is still what you type, what the
    README documents, and what `k250-play` matches first.
    """
    return key.replace("_", " ").capitalize()


def _stim_items():
    """De-duplicated imported stims (key, label, tip, group).

    Two passes: a repeated *name* is dropped, and any stim whose playable timeline
    matches one already kept is dropped as well — so differently named clones of the
    same signal collapse into the single best-named entry.
    """
    with open(CATALOG) as f:
        cat = json.load(f)
    seen, items, sigs = set(), [], {}
    for cname, entries in cat.items():
        for e in entries:
            if not isinstance(e, dict):
                continue
            name = e.get("name") or e.get("type") or ""
            if not name or name in seen:
                continue
            group = NAME_GROUP.get(name, CAT_GROUP.get(cname))
            if not group:
                continue
            steps = len(e.get("sequence") or [])
            typ = e.get("typeEstim", "")
            label = ("Tease & deny " + name) if cname == "TandDCFNM1" \
                else (_data_label(name, e) or pretty(name))
            tip = " · ".join(x for x in (typ, f"{steps} steps" if steps else "") if x) or "imported stim"
            item = {"key": f"{cname}/{name}", "label": label,
                    "tip": tip, "src": "stim", "group": group}
            sig = _timeline_sig(e)
            if sig is not None:
                prev = sigs.get(sig)
                if prev is not None:
                    i, pname = prev
                    if name in KEEP_NAMES and pname not in KEEP_NAMES:
                        items[i] = item          # the better name takes the slot
                        sigs[sig] = (i, name)
                    continue
                sigs[sig] = (len(items), name)
            seen.add(name)
            items.append(item)
    return items


def merged_groups():
    """[{name, items:[{key,label,tip,src}]}] — engine patterns + imported stims."""
    groups = {g: [] for g in GROUP_ORDER}
    for gname, pats in ENGINE_GROUPS:
        for key, tip in pats:
            groups[gname].append({"key": key, "label": _engine_label(key),
                                  "tip": tip, "src": "engine"})
    for it in _stim_items():
        groups[it["group"]].append({"key": it["key"], "label": it["label"],
                                    "tip": it["tip"], "src": "stim"})
    return [{"name": g, "items": groups[g]} for g in GROUP_ORDER]


def resolve(query):
    """One key from a query: an engine pattern name, a stim key (cat/name), or the
    display label the page shows. -> (src, key, label).

    Raises LookupError when the query matches nothing, or matches more than one key
    (the candidates come back in the message rather than a silent wrong guess).
    """
    q = (query or "").strip()
    if not q:
        raise LookupError("no key given")
    items = [it for g in merged_groups() for it in g["items"]]
    for it in items:                                  # exact key or pattern name
        if it["key"] == q:
            return it["src"], it["key"], it["label"]
    lq = q.lower()
    for pred in (lambda l: l == lq, lambda l: l.startswith(lq), lambda l: lq in l):
        hits = [it for it in items if pred(it["label"].lower())]
        if len(hits) == 1:
            return hits[0]["src"], hits[0]["key"], hits[0]["label"]
        if len(hits) > 1:
            cand = "; ".join(f"{h['label']} [{h['key']}]" for h in hits[:8])
            raise LookupError(f"'{q}' matches {len(hits)} keys: {cand}")
    raise LookupError(f"'{q}' matches nothing — run `k250-play --list` to see the keys")


if __name__ == "__main__":
    total = 0
    for g in merged_groups():
        print(f"## {g['name']}  ({len(g['items'])})")
        for it in g["items"]:
            total += 1
            tag = "pattern" if it["src"] == "engine" else "stim   "
            print(f"   {it['label']:<44} {tag} {it['key']}")
    print(f"\n{total} keys — run any of them with:  k250-play <key>")
