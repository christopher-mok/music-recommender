import type { Artist, Track } from '../types';

/*
 * Placeholder audio. There are no recordings in this project, so each song is
 * a short generated loop: an arpeggio over a four-chord progression plus a
 * bass note, with tempo, key and tone taken from the track and its artist.
 * Replace this class with a real player (an <audio> element or a streaming
 * SDK) and keep the same three methods.
 */

const MAJOR = [0, 2, 4, 5, 7, 9, 11];
const MINOR = [0, 2, 3, 5, 7, 8, 10];
const CHORD = [0, 2, 4, 7];
const LOOKAHEAD = 0.4; // seconds of notes scheduled ahead of the clock
const TICK = 100; // ms between scheduler runs

const hz = (midi: number) => 440 * Math.pow(2, (midi - 69) / 12);

export class Synth {
  private ac: AudioContext | null = null;
  private out: GainNode | null = null;
  private timer: number | undefined;
  private step = 0;
  private nextT = 0;
  private track: Track | null = null;
  private artist: Artist | null = null;

  /** Must be called from a user gesture the first time, per browser autoplay rules. */
  private ensure(): AudioContext | null {
    if (!this.ac) {
      const AC: typeof AudioContext | undefined =
        window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (!AC) return null;
      try {
        this.ac = new AC();
        this.out = this.ac.createGain();
        const lowpass = this.ac.createBiquadFilter();
        lowpass.type = 'lowpass';
        lowpass.frequency.value = 2400;
        this.out.connect(lowpass);
        lowpass.connect(this.ac.destination);
      } catch {
        this.ac = null;
        return null;
      }
    }
    if (this.ac.state === 'suspended') void this.ac.resume();
    return this.ac;
  }

  play(track: Track, artist: Artist, volume: number) {
    this.track = track;
    this.artist = artist;
    this.step = 0;
    this.nextT = 0;
    const ac = this.ensure();
    if (!ac || !this.out) return;
    this.out.gain.cancelScheduledValues(ac.currentTime);
    this.out.gain.setValueAtTime(volume * 0.6, ac.currentTime);
    if (this.timer === undefined) this.timer = window.setInterval(() => this.schedule(), TICK);
    this.schedule();
  }

  pause() {
    if (this.timer !== undefined) {
      window.clearInterval(this.timer);
      this.timer = undefined;
    }
    if (this.ac && this.out) this.out.gain.setTargetAtTime(0, this.ac.currentTime, 0.03);
  }

  setVolume(volume: number) {
    if (this.ac && this.out && this.timer !== undefined) this.out.gain.setValueAtTime(volume * 0.6, this.ac.currentTime);
  }

  private tone(freq: number, at: number, len: number, type: OscillatorType, peak: number) {
    if (!this.ac || !this.out) return;
    const osc = this.ac.createOscillator();
    const gain = this.ac.createGain();
    osc.type = type;
    osc.frequency.value = freq;
    gain.gain.setValueAtTime(0.0001, at);
    gain.gain.linearRampToValueAtTime(peak, at + 0.02);
    gain.gain.exponentialRampToValueAtTime(0.0001, at + len);
    osc.connect(gain);
    gain.connect(this.out);
    osc.start(at);
    osc.stop(at + len + 0.05);
  }

  private schedule() {
    const { ac, track, artist } = this;
    if (!ac || !track || !artist) return;
    const scale = artist.minor ? MINOR : MAJOR;
    const semi = (degree: number) => scale[degree % 7] + 12 * Math.floor(degree / 7);
    const stepLen = 60 / artist.bpm / 2;
    const peak = artist.wave === 'sine' || artist.wave === 'triangle' ? 0.16 : 0.07;
    if (!this.nextT || this.nextT < ac.currentTime) this.nextT = ac.currentTime + 0.06;

    while (this.nextT < ac.currentTime + LOOKAHEAD) {
      const i = this.step % 8;
      const degree = track.prog[Math.floor(this.step / 8) % 4];
      this.tone(hz(track.root + 12 + semi(degree + CHORD[artist.pat[i]])), this.nextT, stepLen * 1.8, artist.wave, peak);
      if (i === 0 || i === 4) this.tone(hz(track.root - 12 + semi(degree)), this.nextT, stepLen * 3.6, 'sine', 0.24);
      this.step += 1;
      this.nextT += stepLen;
    }
  }
}
