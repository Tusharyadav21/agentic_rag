import { ChatView } from "@/components/app/chat-view"

export default async function ChatPage({
  params,
}: {
  params: Promise<{ slug?: string[] }>
}) {
  const resolvedParams = await params
  const conversationId = resolvedParams.slug?.[0] || null
  return <ChatView conversationId={conversationId} />
}
