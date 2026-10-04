"""Compose the built-in tracks from oscillators and noise; no external samples.

Requires NumPy and FFmpeg. Outputs ten-minute soundtracks and short previews.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile
import wave
import numpy as np

RATE = 32000
OUT = Path(__file__).resolve().parents[1] / 'audio' / 'stuck-frame'
TRACKS = [('drum-and-bass', 174), ('electro', 128), ('techno', 132), ('chill', 90)]

def compose(style, bpm):
    rng = np.random.default_rng(20261004 + bpm)
    beat = 60 / bpm
    bars = 16
    length = int(round(bars * 4 * beat * RATE))
    mix = np.zeros((length, 2), dtype=np.float64)

    def add(sound, position, gain=1, pan=0):
        start = int(round(position * beat * RATE)) % length
        stereo = np.column_stack((sound * np.sqrt((1-pan)/2), sound * np.sqrt((1+pan)/2))) * gain
        # Wrap tails into the start, making the whole arrangement loop cleanly.
        for offset in range(0, len(sound), length):
            part = stereo[offset:offset+length]
            first = min(len(part), length-start)
            mix[start:start+first] += part[:first]
            mix[:len(part)-first] += part[first:]

    def time(seconds): return np.arange(int(seconds * RATE)) / RATE
    def kick():
        t = time(.42)
        frequency = 44 + 125 * np.exp(-t*28)
        body = np.sin(2*np.pi*np.cumsum(frequency)/RATE) * np.exp(-t*12)
        return .95*body + .10*rng.normal(size=len(t))*np.exp(-t*180)
    def snare():
        t = time(.23)
        noise = rng.normal(size=len(t))
        high = noise - np.roll(noise, 1)
        return .28*high*np.exp(-t*24) + .32*np.sin(2*np.pi*185*t)*np.exp(-t*30)
    def hat(opened=False):
        t = time(.19 if opened else .07)
        noise = rng.normal(size=len(t))
        high = noise - np.roll(noise, 1)
        return high*np.exp(-t*(24 if opened else 75))*.13
    def note(midi, duration, kind='bass'):
        t = time(duration)
        f = 440 * 2**((midi-69)/12)
        attack = np.minimum(1, t / (.025 if kind == 'pad' else .006))
        release = np.minimum(1, (duration-t)/(.22 if kind == 'pad' else .035))
        if kind == 'bass':
            tone = np.sin(2*np.pi*f*t) + .25*np.sin(2*np.pi*2*f*t) + .12*np.sin(2*np.pi*3*f*t)
            envelope = np.exp(-t*1.8)
        elif kind == 'pad':
            tone = .50*np.sin(2*np.pi*f*t) + .22*np.sin(2*np.pi*f*1.003*t) + .15*np.sin(2*np.pi*2*f*t)
            envelope = np.ones(len(t))
        else:
            tone = np.sin(2*np.pi*f*t) + .30*np.sin(2*np.pi*2*f*t) + .12*np.sin(2*np.pi*4*f*t)
            envelope = np.exp(-t*(7 if kind == 'pluck' else 4))
        return tone * attack * release * envelope

    for bar in range(bars):
        pos = bar * 4
        # An original minor progression, with occasional drum and melody variations.
        root = [33, 29, 36, 31][(bar//4)%4]
        if style == 'drum-and-bass':
            kicks = [0, 1.75, 2.5] if bar%2 == 0 else [0, 2.25, 3.5]
            snares = [1, 3]
            hats = np.arange(0,4,.5)
            bass_positions = [0,.75,1.5,2.5,3.25]
            bass_notes = [root,root+12,root,root+7,root+10]
        elif style == 'electro':
            kicks = [0,1.5,2,3.5]
            snares = [1,3]
            hats = np.arange(0,4,.5)
            bass_positions = [0,.75,1.5,2,2.75,3.5]
            bass_notes = [root,root+12,root+7,root,root+10,root+7]
        elif style == 'techno':
            kicks = [0,1,2,3]
            snares = [1,3]
            hats = np.arange(.5,4,1)
            bass_positions = [.5,1.5,2.5,3.5]
            bass_notes = [root]*4
        else:
            kicks = [0,2.5]
            snares = [1,3]
            hats = np.arange(0,4,.5)
            bass_positions = [0,2]
            bass_notes = [root,root+7]
        for p in kicks: add(kick(),pos+p,.80 if style!='chill' else .70)
        for p in snares: add(snare(),pos+p,.65 if style!='chill' else .50,.04)
        # A consistent, short woodblock pulse keeps the tempo legible underneath
        # syncopated breakbeats and sustained pads.
        pulse_t = time(.10)
        pulse = np.sin(2*np.pi*820*pulse_t)*np.exp(-pulse_t*45)
        for p in [0,1,2,3]: add(pulse,pos+p,.85)
        for n,p in enumerate(hats):
            swing = .07 if style=='chill' and n%2 else 0
            add(hat(style=='techno'),pos+p+swing,1.5 if n%2==0 else .30,(-1)**n*.35)
        if style in ('drum-and-bass','electro') and bar%4 == 3:
            add(snare(),pos+3.75,.22,-.1)
        for p,midi in zip(bass_positions,bass_notes):
            add(note(midi,beat*(1.7 if style=='chill' else .52)),pos+p,.23)
        # Quiet chord beds and stereo delayed melodic accents.
        if style != 'techno':
            for interval in [0,3,7,10]:
                add(note(root+24+interval,beat*4,'pad'),pos,.035 if style=='chill' else .025,(interval-5)/16)
        melody = [0,7,10,3,12,7,3,10]
        for n in range(4 if style=='chill' else 8):
            p = n*(1 if style=='chill' else .5)
            midi = root+36+melody[(n+bar)%len(melody)]
            sound = note(midi,beat*.9,'pluck')
            gain = .08 if style=='techno' else .10
            add(sound,pos+p,gain,(-1)**n*.32)
            add(sound,pos+p+.75,gain*.28,-(-1)**n*.5)
            add(sound,pos+p+1.5,gain*.10,(-1)**n*.5)

    # Gentle limiting, followed by fixed peak headroom.
    mix = np.tanh(mix*1.3)
    mix *= .88/max(np.max(np.abs(mix)),1e-9)
    return (mix*32767).astype('<i2')

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ffmpeg',default='ffmpeg')
    args = parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        for style,bpm in TRACKS:
            pcm = compose(style,bpm)
            wav = Path(temp)/(style+'.wav')
            with wave.open(str(wav),'wb') as handle:
                handle.setnchannels(2);handle.setsampwidth(2);handle.setframerate(RATE);handle.writeframes(pcm.tobytes())
            common = [args.ffmpeg,'-hide_banner','-loglevel','error','-y']
            subprocess.run(common+['-stream_loop','-1','-i',str(wav),'-t','600','-af','afade=t=in:d=0.015',
                '-c:a','libmp3lame','-b:a','96k','-metadata',f'title=Radio RRR {style} {bpm} BPM',
                '-metadata','artist=Radio RRR','-metadata','copyright=CC0 1.0',str(OUT/(style+'.mp3'))],check=True)
            subprocess.run(common+['-i',str(wav),'-t','16','-af','afade=t=out:st=14:d=2',
                '-c:a','libmp3lame','-b:a','96k',str(OUT/(style+'-preview.mp3'))],check=True)
            print(style,bpm,'BPM',flush=True)

if __name__ == '__main__': main()
