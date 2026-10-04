def _estimate_bpm_from_wav(path):
    """Estimate tempo from a short mono PCM WAV using onset-energy autocorrelation."""
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())

    if channels != 1 or sample_width != 2 or rate <= 0:
        raise RuntimeError("Unexpected audio format returned by FFmpeg")

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()

    if len(samples) < rate * 8:
        raise RuntimeError("The stream did not provide enough audio to estimate BPM")

    frame_size = 512
    energies = []
    for start in range(0, len(samples) - frame_size, frame_size):
        chunk = samples[start:start + frame_size]
        total = 0.0
        for sample in chunk:
            value = float(sample) / 32768.0
            total += value * value
        energies.append(math.sqrt(total / frame_size))

    if len(energies) < 80:
        raise RuntimeError("The stream did not provide enough rhythmic audio")

    log_energy = [math.log(max(value, 1e-8)) for value in energies]
    onset = [0.0]
    for index in range(1, len(log_energy)):
        onset.append(max(0.0, log_energy[index] - log_energy[index - 1]))

    smoothed = []
    for index in range(len(onset)):
        left = max(0, index - 1)
        right = min(len(onset), index + 2)
        smoothed.append(sum(onset[left:right]) / (right - left))

    mean = sum(smoothed) / len(smoothed)
    envelope = [value - mean for value in smoothed]
    envelope_energy = sum(value * value for value in envelope)
    if envelope_energy <= 1e-8:
        raise RuntimeError("No stable beat was detected in this sample")

    seconds_per_frame = frame_size / float(rate)
    min_bpm = 60.0
    max_bpm = 190.0
    min_lag = max(1, int(round(60.0 / (max_bpm * seconds_per_frame))))
    max_lag = min(len(envelope) // 2, int(round(60.0 / (min_bpm * seconds_per_frame))))

    candidates = []
    for lag in range(min_lag, max_lag + 1):
        left = envelope[:-lag]
        right = envelope[lag:]
        numerator = sum(a * b for a, b in zip(left, right))
        denom_left = sum(a * a for a in left)
        denom_right = sum(b * b for b in right)
        denominator = math.sqrt(max(denom_left * denom_right, 1e-12))
        correlation = numerator / denominator
        bpm = 60.0 / (lag * seconds_per_frame)

        # Electronic dance music commonly produces strong half-time peaks. Give
        # the musically useful 100-160 BPM range a small tie-break preference,
        # without preventing genuine slower/faster results.
        preference = 1.0
        if 100.0 <= bpm <= 160.0:
            preference = 1.08
        elif 80.0 <= bpm < 100.0 or 160.0 < bpm <= 180.0:
            preference = 1.03

        candidates.append((correlation * preference, correlation, bpm))

    if not candidates:
        raise RuntimeError("No stable beat was detected in this sample")

    candidates.sort(reverse=True)
    best_score, best_corr, best_bpm = candidates[0]

    # If the strongest result is below 90 BPM and its double has nearly the same
    # periodic support, prefer the doubled tempo. This reduces common 70-vs-140
    # half-time errors on dance streams.
    if best_bpm < 90.0:
        doubled = min(candidates, key=lambda item: abs(item[2] - (best_bpm * 2.0)))
        if doubled[1] >= best_corr * 0.72:
            best_score, best_corr, best_bpm = doubled

    distinct = [item for item in candidates[1:] if abs(item[2] - best_bpm) >= 3.0]
    second_corr = distinct[0][1] if distinct else 0.0
    confidence = max(0.0, min(1.0, (best_corr * 0.7) + max(0.0, best_corr - second_corr) * 1.5))

    if best_corr < 0.05:
        raise RuntimeError("No stable beat was detected in this sample")

    return round(best_bpm), round(confidence, 2)
