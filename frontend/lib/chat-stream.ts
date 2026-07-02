import { apiUrl, type ChatRequest, type Conversation, type Source } from "./api"

interface StreamEvent {
  event: string
  data: string
}

function parseSseEvents(buffer: string): {
  events: StreamEvent[]
  rest: string
} {
  const chunks = buffer.split("\n\n")
  const rest = chunks.pop() ?? ""
  const events = chunks
    .map((chunk) => {
      let event = "message"
      const data: string[] = []

      for (const line of chunk.split("\n")) {
        if (line.startsWith("event:")) event = line.slice("event:".length).trim()
        if (line.startsWith("data:")) data.push(line.slice("data:".length).trimStart())
      }

      return { event, data: data.join("\n") }
    })
    .filter((event) => event.data)

  return { events, rest }
}

export interface StreamChatConfig {
  projectId?: string
  token: string
  request: ChatRequest
  individualChat?: boolean
  onConversation?: (conversation: Conversation) => void
  onSources?: (sources: Source[]) => void
  onToken?: (tokenChunk: string) => void
  onNode?: (nodeName: string) => void
  onFinal?: (payload: { message_id: string; content: string }) => void
}

export async function streamChat({
  projectId,
  token,
  request,
  individualChat = false,
  onConversation,
  onSources,
  onToken,
  onNode,
  onFinal,
}: StreamChatConfig): Promise<void> {
  const endpoint = individualChat
    ? "/api/chat"
    : `/api/projects/${projectId}/chat`
  const response = await fetch(apiUrl(endpoint), {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  })

  if (!response.ok || !response.body) {
    let message = "Chat failed"
    try {
      const payload = JSON.parse(await response.text()) as { detail?: string }
      message = payload.detail || message
    } catch {
      // Keep the generic message if the backend did not return JSON.
    }
    throw new Error(message)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""

  while (true) {
    const { value, done } = await reader.read()
    if (done) break

    buffer += decoder.decode(value, { stream: true })
    const parsed = parseSseEvents(buffer)
    buffer = parsed.rest

    for (const eventChunk of parsed.events) {
      if (eventChunk.event === "conversation") {
        const conversation = JSON.parse(eventChunk.data) as Conversation
        onConversation?.(conversation)
      } else if (eventChunk.event === "sources") {
        const sources = JSON.parse(eventChunk.data) as Source[]
        onSources?.(sources)
      } else if (eventChunk.event === "token") {
        const tokenChunk = JSON.parse(eventChunk.data) as string
        onToken?.(tokenChunk)
      } else if (eventChunk.event === "node") {
        const nodeName = JSON.parse(eventChunk.data) as string
        onNode?.(nodeName)
      } else if (eventChunk.event === "final") {
        const payload = JSON.parse(eventChunk.data) as { message_id: string; content: string }
        onFinal?.(payload)
      } else if (eventChunk.event === "error") {
        const payload = JSON.parse(eventChunk.data) as { detail: string }
        throw new Error(payload.detail)
      }
    }
  }
}
