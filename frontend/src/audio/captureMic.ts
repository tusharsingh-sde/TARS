import pcmWorkletUrl from './pcmWorklet.ts?worker&url'
import { PCM_CHUNK_SAMPLES, TARGET_SAMPLE_RATE } from './pcmWorklet.ts'

export interface MicCaptureHandle {
  stop: () => void
}

type AudioContextCtor = new (contextOptions?: AudioContextOptions) => AudioContext

function getAudioContextCtor(): AudioContextCtor {
  const g = globalThis as {
    AudioContext?: AudioContextCtor
    webkitAudioContext?: AudioContextCtor
  }
  const Ctor = g.AudioContext ?? g.webkitAudioContext
  if (!Ctor) {
    throw new Error('AudioContext is not supported in this browser')
  }
  return Ctor
}

function createTargetAudioContext(): AudioContext {
  const Ctor = getAudioContextCtor()
  try {
    return new Ctor({ sampleRate: TARGET_SAMPLE_RATE })
  } catch {
    return new Ctor()
  }
}

export async function startMicCapture(
  onChunk: (pcm16Chunk: ArrayBuffer) => void,
): Promise<MicCaptureHandle> {
  if (!navigator.mediaDevices?.getUserMedia) {
    throw new Error('getUserMedia is not available in this browser')
  }

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
    },
  })

  let ctx: AudioContext | null = null
  let source: MediaStreamAudioSourceNode | null = null
  let workletNode: AudioWorkletNode | null = null
  let silentGain: GainNode | null = null

  const cleanup = () => {
    if (workletNode) {
      workletNode.port.onmessage = null
      try {
        workletNode.disconnect()
      } catch {
        /* already disconnected */
      }
    }
    if (source) {
      try {
        source.disconnect()
      } catch {
        /* already disconnected */
      }
    }
    if (silentGain) {
      try {
        silentGain.disconnect()
      } catch {
        /* already disconnected */
      }
    }
    for (const track of stream.getTracks()) {
      track.stop()
    }
    if (ctx && ctx.state !== 'closed' && typeof ctx.close === 'function') {
      void ctx.close()
    }
  }

  try {
    ctx = createTargetAudioContext()
    if (ctx.state === 'suspended' && typeof ctx.resume === 'function') {
      await ctx.resume()
    }
    await ctx.audioWorklet.addModule(pcmWorkletUrl)

    source = ctx.createMediaStreamSource(stream)
    workletNode = new AudioWorkletNode(ctx, 'tars-pcm-worklet', {
      numberOfInputs: 1,
      numberOfOutputs: 1,
      outputChannelCount: [1],
      channelCount: 1,
      processorOptions: {
        targetSampleRate: TARGET_SAMPLE_RATE,
        chunkSamples: PCM_CHUNK_SAMPLES,
      },
    })
    workletNode.port.onmessage = (ev: MessageEvent<ArrayBuffer>) => {
      if (ev.data instanceof ArrayBuffer && ev.data.byteLength > 0) {
        onChunk(ev.data)
      }
    }

    source.connect(workletNode)
    if (typeof ctx.createGain === 'function' && ctx.destination) {
      silentGain = ctx.createGain()
      silentGain.gain.value = 0
      workletNode.connect(silentGain)
      silentGain.connect(ctx.destination)
    }

    return { stop: cleanup }
  } catch (err) {
    cleanup()
    throw err
  }
}
