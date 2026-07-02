"use client"

import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react"
import { useSearchParams, useRouter } from "next/navigation"

import { useProjects } from "@/hooks/use-projects"
import { useDocuments } from "@/hooks/use-documents"
import { useChat } from "@/hooks/use-chat"
import { toast } from "@/components/ui/toast"
import { MAX_UPLOAD_SIZE, ACCEPTED_FILE_TYPES } from "@/lib/constants"

import { DEFAULT_OLLAMA_SETTINGS } from "@/lib/api"
import { MessageList } from "@/components/chat/message-list"
import { ChatInput } from "@/components/chat/chat-input"
import { HistoryView } from "@/components/chat/history-view"
import { PromptsView } from "@/components/chat/prompts-view"
import { SourcesView } from "@/components/chat/sources-view"

export function ChatView({ conversationId }: { conversationId?: string | null }) {
  const searchParams = useSearchParams()
  const searchQuery = searchParams?.get("search") || ""
  const router = useRouter()

  const { activeProject, activeProjectId, projects, isSavingProject, updateProjectSettings, selectProject } = useProjects()
  const {
    documents,
    selectedDocumentIds,
    isUploading,
    toggleDocumentScope,
    uploadDocument,
    deleteDocument,
  } = useDocuments()
  const {
    conversations,
    activeConversationId,
    messages,
    sources,
    feedbackByMessageId,
    pendingFeedbackId,
    isLoadingMessages,
    isStreaming,
    executingNode,
    openConversation,
    submitMessage,
    sendFeedback,
    conversationsTotal,
    chatMode,
    setChatMode,
  } = useChat()

  const [centerTab, setCenterTab] = useState<"chat" | "sources" | "history" | "prompts">("chat")
  const isCanvasMode = false
  const [expandedSourceId, setExpandedSourceId] = useState<string | null>(null)
  const [question, setQuestion] = useState("")
  const [webSearchEnabled, setWebSearchEnabled] = useState(false)
  const [selectedModel, setSelectedModel] = useState(DEFAULT_OLLAMA_SETTINGS.localModel)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const formRef = useRef<HTMLFormElement>(null)

  // Load conversation from URL path on mount
  useEffect(() => {
    if (conversationId && activeProjectId) {
      void openConversation(conversationId)
      router.push(`/chat/${conversationId}`, { scroll: false })
    }
  }, [conversationId]) // eslint-disable-line react-hooks/exhaustive-deps

  const activeInContextDocuments = useMemo(() => {
    const completedDocs = documents.filter((doc) => doc.status === "completed")
    return completedDocs.filter((doc) => selectedDocumentIds.includes(doc.id))
  }, [documents, selectedDocumentIds])

  const handleModeChange = (mode: "individual" | "project", projectId?: string) => {
    setChatMode(mode)
    if (mode === "project" && projectId) {
      void selectProject(projectId)
    } else if (mode === "individual") {
      // Clear any active project context for individual chat
      setCenterTab("chat")
    }
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    if (!question.trim()) return
    const currentQuestion = question
    setQuestion("")
    await submitMessage(currentQuestion, selectedModel)
    setExpandedSourceId(null)
  }

  const handleSuggestionClick = (text: string) => {
    setQuestion(text)
  }

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return
    if (file.size > MAX_UPLOAD_SIZE) {
      toast.error("File exceeds the 10MB limit")
      e.target.value = ""
      return
    }
    void uploadDocument(file)
    e.target.value = ""
  }

  return (
    <div className="flex-1 flex overflow-hidden h-full bg-background min-h-0 relative">
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileChange}
        className="hidden"
        accept={ACCEPTED_FILE_TYPES}
      />

      <div className="flex-1 flex flex-col min-w-0 h-full">
        <div className="flex-1 overflow-hidden min-h-0 flex flex-col">
          {centerTab === "chat" && (
            <div className="flex-1 flex flex-col overflow-hidden min-h-0 relative bg-background">
              {isCanvasMode ? (
                <div className="flex-1 flex h-full">
                  <div className="w-1/2 border-r border-border h-full flex flex-col p-6">
                    <div className="flex-1 border border-dashed border-border rounded-xl flex items-center justify-center text-muted-foreground text-sm font-medium">
                      Conversation History
                    </div>
                  </div>
                  <div className="w-1/2 h-full flex flex-col p-6 bg-card">
                    <div className="flex-1 border border-border bg-muted rounded-xl flex items-center justify-center text-muted-foreground text-sm font-medium shadow-sm">
                      Generated Canvas Content
                    </div>
                  </div>
                </div>
              ) : (
                <MessageList
                  messages={messages}
                  sources={sources}
                  documents={documents}
                  isLoadingMessages={isLoadingMessages}
                  isStreaming={isStreaming}
                  executingNode={executingNode}
                  feedbackByMessageId={feedbackByMessageId}
                  pendingFeedbackId={pendingFeedbackId}
                  expandedSourceId={expandedSourceId}
                  activeProjectId={activeProjectId}
                  activeProjectName={activeProject?.name}
                  conversationsTotal={conversationsTotal}
                  onToggleSources={(id) => setExpandedSourceId(expandedSourceId === id ? null : id)}
                  onSendFeedback={(id, type) => void sendFeedback(id, type)}
                  onSourceClick={(id) => setExpandedSourceId(id)}
                  onSuggestionClick={handleSuggestionClick}
                />
              )}

              {!isCanvasMode && (
                <ChatInput
                  ref={formRef}
                  question={question}
                  onQuestionChange={setQuestion}
                  onSubmit={handleSubmit}
                  onLibraryClick={() => setCenterTab("sources")}
                  isStreaming={isStreaming}
                  isLoadingMessages={isLoadingMessages}
                  hasActiveProject={!!activeProject}
                  webSearchEnabled={webSearchEnabled}
                  onWebSearchToggle={() => setWebSearchEnabled(!webSearchEnabled)}
                  selectedModel={selectedModel}
                  onModelChange={setSelectedModel}
                  projects={projects}
                  chatMode={chatMode}
                  activeProjectId={activeProjectId}
                  onModeChange={handleModeChange}
                />
              )}
            </div>
          )}

          {centerTab === "history" && (
            <HistoryView
              conversations={conversations}
              activeConversationId={activeConversationId}
              searchQuery={searchQuery}
              onOpenConversation={(id) => void openConversation(id)}
              onNavigateToChat={() => setCenterTab("chat")}
            />
          )}

          {centerTab === "prompts" && (
            <PromptsView
              activeProject={activeProject}
              isSavingProject={isSavingProject}
              onSaveSystemPrompt={(prompt) => void updateProjectSettings(prompt)}
            />
          )}

          {centerTab === "sources" && (
            <SourcesView
              documents={documents}
              selectedDocumentIds={selectedDocumentIds}
              activeInContextDocuments={activeInContextDocuments}
              isUploading={isUploading}
              onBack={() => setCenterTab("chat")}
              onUpload={() => fileInputRef.current?.click()}
              onToggleScope={(id) => toggleDocumentScope(id)}
              onDelete={(id) => deleteDocument(id)}
            />
          )}
        </div>
      </div>
    </div>
  )
}
