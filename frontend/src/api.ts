import type { Meta } from './types'

// Consumes the /ask Server-Sent Events stream. The backend emits three named
// events (see app/service.py): `meta` once with citations + refusal flag,
// zero or more `token` deltas, then `done`. We POST and read the response body
// as a stream rather than using EventSource, because EventSource is GET-only
// and auto-reconnects on close — which would re-fire the whole query after
// `done`. Returns an abort function to cancel an in-flight request.

export interface StreamHandlers {
  onMeta: (meta: Meta) => void
  onToken: (text: string) => void
  onDone: () => void
  onError: (err: Error) => void
}

export function askStream(query: string, handlers: StreamHandlers): () => void {
  const controller = new AbortController()

  void (async () => {
    try {
      const res = await fetch('/ask', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query }),
        signal: controller.signal,
      })
      if (!res.ok || !res.body) {
        throw new Error(`Request failed (${res.status} ${res.statusText})`)
      }

      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      // Frames are separated by a blank line; parse each as it completes.
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })
        let sep: number
        while ((sep = buffer.indexOf('\n\n')) !== -1) {
          dispatch(buffer.slice(0, sep), handlers)
          buffer = buffer.slice(sep + 2)
        }
      }
      // Safety net if the connection closes without an explicit `done` event.
      handlers.onDone()
    } catch (err) {
      if ((err as Error).name === 'AbortError') return
      handlers.onError(err as Error)
    }
  })()

  return () => controller.abort()
}

function dispatch(frame: string, h: StreamHandlers): void {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trimStart())
  }
  if (dataLines.length === 0) return

  const data = JSON.parse(dataLines.join('\n'))
  if (event === 'meta') h.onMeta(data as Meta)
  else if (event === 'token') h.onToken((data as { text: string }).text)
  else if (event === 'done') h.onDone()
}
