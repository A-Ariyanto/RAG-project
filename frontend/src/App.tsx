import { useEffect, useRef, useState } from 'react'
import { askStream } from './api'
import type { Citation, Meta } from './types'

interface UserMsg {
  role: 'user'
  text: string
}

interface AssistantMsg {
  role: 'assistant'
  text: string
  citations: Citation[]
  refused: boolean
  pending: boolean
  error?: string
}

type Message = UserMsg | AssistantMsg

const EXAMPLES = [
  'In which terms is COMP3311 offered?',
  "I've done COMP1531 and COMP2521 — can I enrol in COMP3311?",
  'What are the prerequisites for COMP6771?',
  'How many UOC is a standard Computer Science degree?',
]

function App() {
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState('')
  const [streaming, setStreaming] = useState(false)
  const cancelRef = useRef<(() => void) | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages])

  useEffect(() => () => cancelRef.current?.(), [])

  // Patch the most recent assistant message in place as the stream progresses.
  const patchAssistant = (fn: (m: AssistantMsg) => AssistantMsg) => {
    setMessages((prev) => {
      const next = [...prev]
      for (let i = next.length - 1; i >= 0; i--) {
        if (next[i].role === 'assistant') {
          next[i] = fn(next[i] as AssistantMsg)
          break
        }
      }
      return next
    })
  }

  const restart = () => {
    cancelRef.current?.()
    cancelRef.current = null
    setMessages([])
    setInput('')
    setStreaming(false)
  }

  const submit = (question: string) => {
    const q = question.trim()
    if (!q || streaming) return

    setMessages((prev) => [
      ...prev,
      { role: 'user', text: q },
      { role: 'assistant', text: '', citations: [], refused: false, pending: true },
    ])
    setInput('')
    setStreaming(true)

    cancelRef.current = askStream(q, {
      onMeta: (meta: Meta) =>
        patchAssistant((m) => ({ ...m, citations: meta.citations, refused: meta.refused })),
      onToken: (text: string) => patchAssistant((m) => ({ ...m, text: m.text + text })),
      onDone: () => {
        patchAssistant((m) => ({ ...m, pending: false }))
        setStreaming(false)
        cancelRef.current = null
      },
      onError: (err: Error) => {
        patchAssistant((m) => ({ ...m, pending: false, error: err.message }))
        setStreaming(false)
        cancelRef.current = null
      },
    })
  }

  return (
    <div className="flex h-full flex-col">
      <header className="bg-unsw-yellow border-b border-unsw-black/10">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-4 py-4">
          <div>
            <h1 className="text-xl font-extrabold tracking-tight text-unsw-black">
              UNSW Handbook Assistant
            </h1>
            <p className="text-sm font-medium text-unsw-black/70">
              Grounded answers to CSE course &amp; enrolment questions, with citations.
            </p>
          </div>
          {messages.length > 0 && (
            <button
              type="button"
              onClick={restart}
              className="shrink-0 rounded-lg border border-unsw-black/30 px-3 py-1.5 text-sm font-semibold text-unsw-black transition hover:bg-unsw-black hover:text-white"
            >
              New chat
            </button>
          )}
        </div>
      </header>

      <main ref={scrollRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto flex max-w-3xl flex-col gap-4 px-4 py-6">
          {messages.length === 0 ? (
            <EmptyState onPick={submit} disabled={streaming} />
          ) : (
            messages.map((m, i) =>
              m.role === 'user' ? (
                <UserBubble key={i} text={m.text} />
              ) : (
                <AssistantBubble key={i} msg={m} />
              ),
            )
          )}
        </div>
      </main>

      <footer className="border-t border-line bg-white">
        <form
          className="mx-auto flex max-w-3xl items-end gap-2 px-4 py-3"
          onSubmit={(e) => {
            e.preventDefault()
            submit(input)
          }}
        >
          <input
            className="min-w-0 flex-1 rounded-lg border border-line bg-paper px-4 py-3 text-ink outline-none focus:border-unsw-black/40"
            placeholder="Ask about a CSE course or enrolment rule…"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            disabled={streaming}
          />
          <button
            type="submit"
            className="shrink-0 rounded-lg bg-unsw-yellow px-5 py-3 font-semibold text-unsw-black transition hover:bg-unsw-yellow-dark disabled:cursor-not-allowed disabled:opacity-50"
            disabled={streaming || !input.trim()}
          >
            {streaming ? 'Asking…' : 'Ask'}
          </button>
        </form>
      </footer>
    </div>
  )
}

function EmptyState({ onPick, disabled }: { onPick: (q: string) => void; disabled: boolean }) {
  return (
    <div className="mt-8 text-center">
      <h2 className="text-lg font-semibold text-ink">Ask about UNSW CSE courses</h2>
      <p className="mx-auto mt-1 max-w-md text-sm text-muted">
        Answers come only from the UNSW Handbook. If the handbook doesn&apos;t cover it,
        the assistant says so rather than guessing.
      </p>
      <div className="mt-5 flex flex-col gap-2">
        {EXAMPLES.map((q) => (
          <button
            key={q}
            type="button"
            disabled={disabled}
            onClick={() => onPick(q)}
            className="rounded-lg border border-line bg-white px-4 py-2.5 text-left text-sm text-ink transition hover:border-unsw-black/30 hover:bg-paper disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  )
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-unsw-black px-4 py-2.5 text-white">
        {text}
      </div>
    </div>
  )
}

function AssistantBubble({ msg }: { msg: AssistantMsg }) {
  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[92%] rounded-2xl rounded-bl-sm border border-line bg-white px-4 py-3">
        {msg.refused && (
          <span className="mb-2 inline-block rounded bg-unsw-yellow px-2 py-0.5 text-xs font-semibold text-unsw-black">
            Not enough information
          </span>
        )}

        <div className="whitespace-pre-wrap leading-relaxed text-ink">
          <AnswerText text={msg.text} citations={msg.citations} />
          {msg.pending && <Caret />}
        </div>

        {msg.error && (
          <p className="mt-2 text-sm text-red-600">Something went wrong: {msg.error}</p>
        )}

        {msg.citations.length > 0 && <Sources citations={msg.citations} />}
      </div>
    </div>
  )
}

// Renders answer text, turning inline [n] markers into links to the source
// handbook page. Markers with no matching citation are left as plain text.
function AnswerText({ text, citations }: { text: string; citations: Citation[] }) {
  const byN = new Map(citations.map((c) => [c.n, c]))
  const parts = text.split(/(\[\d+\])/g)
  return (
    <>
      {parts.map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part)
        const c = m ? byN.get(Number(m[1])) : undefined
        if (c) {
          return (
            <a
              key={i}
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
              title={`${c.doc_code} — ${c.title}`}
              className="mx-0.5 rounded bg-unsw-yellow/60 px-1 text-xs font-semibold text-unsw-black no-underline hover:bg-unsw-yellow"
            >
              {part}
            </a>
          )
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}

function Sources({ citations }: { citations: Citation[] }) {
  return (
    <div className="mt-3 border-t border-line pt-3">
      <p className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Sources</p>
      <ol className="flex flex-col gap-1">
        {citations.map((c) => (
          <li key={c.n} className="text-sm">
            <a
              href={c.source_url}
              target="_blank"
              rel="noreferrer"
              className="text-ink underline decoration-line underline-offset-2 hover:decoration-unsw-black"
            >
              <span className="font-semibold">[{c.n}] {c.doc_code}</span> — {c.title}{' '}
              <span className="text-muted">({c.section_type})</span>
            </a>
          </li>
        ))}
      </ol>
    </div>
  )
}

function Caret() {
  return <span className="ml-0.5 inline-block h-4 w-2 animate-pulse bg-unsw-black align-text-bottom" />
}

export default App
