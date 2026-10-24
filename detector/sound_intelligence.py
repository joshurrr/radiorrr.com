"""Optional, observational voice-vs-instrumental classification using Discogs-EffNet.

This module shares the genre detector's already-computed embeddings. It does not
change stream selection, genre classification, BPM, speech, or any live relay.
The classifier distinguishes voice in music from instrumental music; it does
NOT separately recognise singing versus speaking, vocal pitch, or harshness.
"""
import json
import math
from pathlib import Path

MODEL_NAME = "voice_instrumental-discogs-effnet-1"
MODEL_FILE = Path('/app/models') / (MODEL_NAME + '.pb')
METADATA_FILE = Path('/app/models') / (MODEL_NAME + '.json')


def load_model(es):
    """Return classifier or None if weights are unavailable or invalid."""
    if not MODEL_FILE.is_file() or not METADATA_FILE.is_file():
        print('[Sound AI] Optional model missing; observational analysis disabled', flush=True)
        return None
    try:
        with METADATA_FILE.open('r', encoding='utf-8') as handle:
            classes = json.load(handle)['classes']
        if classes != ['instrumental', 'voice']:
            raise ValueError(f'Unexpected sound model classes: {classes}')
        model = es.TensorflowPredict2D(
            graphFilename=str(MODEL_FILE),
            output='model/Softmax',
        )
        print('[Sound AI] Voice/instrumental head loaded (shadow mode)', flush=True)
        return model
    except Exception as error:
        print(f'[Sound AI] Optional model failed to load: {error}', flush=True)
        return None


def analyse_embeddings(model, embeddings):
    """Summarise predictions for each model audio window; None when unavailable.

    Probability is NOT a measured proportion of seconds, and the fraction of
    windows classified voice-dominant is only a coarse sampled estimate.
    """
    if model is None:
        return None
    predictions = model(embeddings)
    if getattr(predictions, 'ndim', 0) != 2 or predictions.shape[1] != 2 or predictions.shape[0] < 1:
        raise RuntimeError('Unexpected voice/instrumental model prediction shape')
    rows = []
    for row in predictions:
        instrumental, voice = float(row[0]), float(row[1])
        if not all(math.isfinite(value) and 0 <= value <= 1 for value in (instrumental, voice)):
            raise RuntimeError('Invalid voice/instrumental prediction')
        rows.append((instrumental, voice))
    count = len(rows)
    return {
        'model': MODEL_NAME,
        'vocal_probability': round(sum(row[1] for row in rows) / count, 4),
        'instrumental_probability': round(sum(row[0] for row in rows) / count, 4),
        'vocal_dominant_fraction': round(sum(row[1] >= 0.5 for row in rows) / count, 4),
        'window_count': count,
    }
