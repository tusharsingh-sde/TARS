declare abstract class AudioWorkletProcessor {
  readonly port: MessagePort
  constructor(options?: unknown)
  abstract process(
    inputs: Float32Array[][],
    outputs: Float32Array[][],
    parameters: Record<string, Float32Array>,
  ): boolean
}

declare function registerProcessor(name: string, processorCtor: unknown): void
declare const sampleRate: number

export const TARGET_SAMPLE_RATE = 16000
/** 30 ms at 16 kHz mono = 480 samples (960 bytes PCM16 LE). */
export const PCM_CHUNK_SAMPLES = 480

const AudioWorkletBase: typeof AudioWorkletProcessor =
  typeof AudioWorkletProcessor !== 'undefined'
    ? AudioWorkletProcessor
    : (class {
        readonly port!: MessagePort
      } as unknown as typeof AudioWorkletProcessor)

export function float32ToInt16(sample: number): number {
  const clamped = Math.max(-1, Math.min(1, sample))
  return clamped < 0 ? Math.round(clamped * 0x8000) : Math.round(clamped * 0x7fff)
}

export class Pcm16WorkletProcessor extends AudioWorkletBase {
  private readonly targetRate: number
  private readonly chunkSamples: number
  private outBuffer: ArrayBuffer
  private outView: DataView
  private outOffset = 0
  private phase = 0
  private prevSample = 0
  private hasPrevSample = false

  constructor(options?: { processorOptions?: { targetSampleRate?: number; chunkSamples?: number } }) {
    super(options)
    this.targetRate = options?.processorOptions?.targetSampleRate ?? TARGET_SAMPLE_RATE
    this.chunkSamples = options?.processorOptions?.chunkSamples ?? PCM_CHUNK_SAMPLES
    this.outBuffer = new ArrayBuffer(this.chunkSamples * 2)
    this.outView = new DataView(this.outBuffer)
  }

  private pushSample(floatSample: number): void {
    this.outView.setInt16(this.outOffset * 2, float32ToInt16(floatSample), true)
    this.outOffset += 1
    if (this.outOffset >= this.chunkSamples) {
      const completed = this.outBuffer
      this.outBuffer = new ArrayBuffer(this.chunkSamples * 2)
      this.outView = new DataView(this.outBuffer)
      this.outOffset = 0
      this.port.postMessage(completed, [completed])
    }
  }

  process(inputs: Float32Array[][]): boolean {
    const channel = inputs[0]?.[0]
    if (!channel || channel.length === 0) {
      return true
    }

    const inRate = typeof sampleRate === 'number' && sampleRate > 0 ? sampleRate : this.targetRate
    if (inRate === this.targetRate) {
      for (let i = 0; i < channel.length; i += 1) {
        this.pushSample(channel[i])
      }
      return true
    }

    const step = inRate / this.targetRate
    let pos = this.phase
    while (pos < channel.length - 1) {
      const idx = Math.floor(pos)
      const frac = pos - idx
      const s0 = idx < 0 ? (this.hasPrevSample ? this.prevSample : channel[0]) : channel[idx]
      const s1 = channel[idx + 1]
      this.pushSample(s0 + (s1 - s0) * frac)
      pos += step
    }

    this.phase = pos - channel.length
    this.prevSample = channel[channel.length - 1]
    this.hasPrevSample = true
    return true
  }
}

if (typeof registerProcessor === 'function') {
  registerProcessor('tars-pcm-worklet', Pcm16WorkletProcessor)
}

