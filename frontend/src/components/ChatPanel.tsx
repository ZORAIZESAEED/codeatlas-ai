"use client";

import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type FormEvent, type ReactNode } from "react";
import {
  chatApi,
  formatDate,
  type ChatEvent,
  type ChatMessage,
  type ChatMode,
  type Conversation,
  type ConversationDetail,
  type LLMStatus,
  type MessageCitation,
  type TraceStep,
} from "@/lib/api";
import { highlight } from "@/lib/highlight";
import type { LineRange } from "./CodeViewer";

type OpenSource = (path: string, range: LineRange) => void;

/** A progress line for work the backend reported as actually running. */
type Step = { key: string; label: string; state: "running" | "done" | "failed"; detail?: string };

type Live = {
  question: string;
  text: string;
  steps: Step[];
  error: string | null;
  stopped: boolean;
};

const MODES: { value: ChatMode; label: string; hint: string }[] = [
  {
    value: "investigation",
    label: "Investigate",
    hint: "Retrieve, then follow related code with read-only tools before answering",
  },
  { value: "baseline", label: "Quick answer", hint: "Single retrieval pass, then answer" },
];

export default function ChatPanel({ projectId, onOpen }: { projectId: string; onOpen: OpenSource }) {
  const [status, setStatus] = useState<LLMStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ConversationDetail | null>(null);
  const [mode, setMode] = useState<ChatMode>("investigation");
  const [input, setInput] = useState("");
  const [live, setLive] = useState<Live | null>(null);
  const [error, setError] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const bottom = useRef<HTMLDivElement>(null);
  const toolSeq = useRef(0);

  const loadStatus = useCallback(() => {
    setStatusError(null);
    chatApi
      .llmStatus()
      .then(setStatus)
      .catch((e: unknown) => setStatusError(e instanceof Error ? e.message : "Status check failed."));
  }, []);

  const loadConversations = useCallback(
    () =>
      chatApi
        .conversations(projectId)
        .then(setConversations)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load conversations.")),
    [projectId],
  );

  const loadDetail = useCallback(
    (id: string) =>
      chatApi
        .conversation(projectId, id)
        .then(setDetail)
        .catch((e: unknown) => setError(e instanceof Error ? e.message : "Failed to load conversation.")),
    [projectId],
  );

  useEffect(() => {
    loadStatus();
  }, [loadStatus]);

  useEffect(() => {
    setActiveId(null);
    setDetail(null);
    setLive(null);
    void loadConversations();
    return () => controller.current?.abort();
  }, [projectId, loadConversations]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [detail, live?.text, live?.steps.length]);

  const select = (id: string) => {
    if (live) return;
    setError(null);
    setActiveId(id);
    setDetail(null);
    void loadDetail(id);
  };

  const startNew = () => {
    if (live) return;
    setActiveId(null);
    setDetail(null);
    setError(null);
  };

  const rename = async (c: Conversation) => {
    const title = window.prompt("Rename conversation", c.title)?.trim();
    if (!title || title === c.title) return;
    try {
      await chatApi.renameConversation(projectId, c.id, title);
      await loadConversations();
      if (activeId === c.id) await loadDetail(c.id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Rename failed.");
    }
  };

  const remove = async (c: Conversation) => {
    if (!window.confirm(`Delete the conversation "${c.title}"? Repository files and indexes are not affected.`)) return;
    try {
      await chatApi.deleteConversation(projectId, c.id);
      if (activeId === c.id) startNew();
      await loadConversations();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Delete failed.");
    }
  };

  const onEvent = (event: ChatEvent, conversationRef: { id: string | null }) => {
    const d = event.data;
    const step = (s: Step) =>
      setLive((prev) => (prev ? { ...prev, steps: [...prev.steps.filter((x) => x.key !== s.key), s] } : prev));
    const finish = (key: string, state: Step["state"], detail?: string) =>
      setLive((prev) =>
        prev
          ? { ...prev, steps: prev.steps.map((s) => (s.key === key ? { ...s, state, detail: detail ?? s.detail } : s)) }
          : prev,
      );
    switch (event.type) {
      case "turn_started":
        conversationRef.id = String(d.conversation_id);
        setActiveId(conversationRef.id);
        if (d.created_conversation) void loadConversations();
        break;
      case "retrieval_started":
        step({ key: "retrieval", label: "Searching repository…", state: "running" });
        break;
      case "retrieval_completed":
        finish("retrieval", "done", `${d.evidence_count} source block(s) found`);
        break;
      case "planning":
        step({ key: `plan-${d.step}`, label: "Choosing what to inspect next…", state: "running" });
        break;
      case "tool_started":
        setLive((prev) =>
          prev
            ? { ...prev, steps: prev.steps.map((s) => (s.key.startsWith("plan-") && s.state === "running" ? { ...s, state: "done" } : s)) }
            : prev,
        );
        toolSeq.current += 1;
        step({ key: `tool-${toolSeq.current}`, label: String(d.label), state: "running" });
        break;
      case "tool_completed":
        setLive((prev) => {
          if (!prev) return prev;
          const steps = [...prev.steps];
          const i = steps.map((s) => s.state === "running" && s.key.startsWith("tool-")).lastIndexOf(true);
          if (i >= 0) steps[i] = { ...steps[i], state: d.ok ? "done" : "failed", detail: String(d.summary ?? "") };
          return { ...prev, steps };
        });
        break;
      case "investigation_completed":
        setLive((prev) =>
          prev
            ? { ...prev, steps: prev.steps.map((s) => (s.state === "running" && s.key.startsWith("plan-") ? { ...s, state: "done" } : s)) }
            : prev,
        );
        break;
      case "generation_started":
        step({ key: "generation", label: "Preparing explanation…", state: "running" });
        break;
      case "answer_delta":
        setLive((prev) => (prev ? { ...prev, text: prev.text + String(d.text ?? "") } : prev));
        break;
      case "citation_update":
        finish("generation", "done");
        break;
      case "error":
        setLive((prev) =>
          prev ? { ...prev, error: String(d.message ?? "The answer failed."), stopped: d.status === "cancelled" } : prev,
        );
        break;
    }
  };
  const send = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = input.trim();
    if (!question || live) return;
    const current = new AbortController();
    controller.current = current;
    setError(null);
    setInput("");
    setLive({ question, text: "", steps: [], error: null, stopped: false });
    const ref = { id: activeId };
    let failed: string | null = null;
    try {
      await chatApi.streamChat(projectId, { question, conversation_id: activeId, mode }, (e) => onEvent(e, ref), current.signal);
    } catch (e) {
      if (e instanceof Error && e.name === "AbortError") {
        failed = null;
      } else {
        failed = e instanceof Error ? e.message : "The request failed.";
        if (!ref.id) setInput(question); // nothing was stored; keep the question
      }
    } finally {
      controller.current = null;
    }
    setLive(null);
    if (failed) setError(failed);
    if (ref.id) {
      // After Stop the server records the cancellation a moment later: wait for a final state.
      for (let attempt = 0; attempt < 10; attempt++) {
        const latest = await chatApi.conversation(projectId, ref.id).catch(() => null);
        if (latest) setDetail(latest);
        if (!latest || latest.messages.every((m) => m.status !== "generating")) break;
        await new Promise((resolve) => setTimeout(resolve, 1000));
      }
      void loadConversations();
    }
  };

  const stop = () => {
    controller.current?.abort();
    setLive((prev) => (prev ? { ...prev, stopped: true } : prev));
  };

  const ready = status?.available === true;
  const messages = activeId && detail?.conversation.id === activeId ? detail.messages : [];

  return (
    <div className="chat">
      <aside className="chat-sidebar card">
        <button className="btn btn-primary btn-block" onClick={startNew} disabled={!!live}>
          + New conversation
        </button>
        <h3 className="sidebar-title">History</h3>
        {conversations.length === 0 ? (
          <p className="muted small">No conversations yet.</p>
        ) : (
          <ul className="conv-list">
            {conversations.map((c) => (
              <li key={c.id} className={`conv-item${c.id === activeId ? " active" : ""}`}>
                <button className="conv-open" onClick={() => select(c.id)} disabled={!!live} title={c.title}>
                  <span className="conv-title">{c.title}</span>
                  <span className="muted small">
                    {formatDate(c.updated_at)} · {Math.floor(c.message_count / 2)} question{c.message_count === 2 ? "" : "s"}
                  </span>
                </button>
                <span className="conv-actions">
                  <button className="icon-btn" onClick={() => rename(c)} title="Rename" aria-label={`Rename ${c.title}`}>✎</button>
                  <button className="icon-btn" onClick={() => remove(c)} title="Delete" aria-label={`Delete ${c.title}`}>✕</button>
                </span>
              </li>
            ))}
          </ul>
        )}
      </aside>

      <section className="chat-main">
        <div className="chat-toolbar card">
          <ModelStatus status={status} error={statusError} onRetry={loadStatus} />
          <div className="segmented" role="radiogroup" aria-label="Answer mode">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={mode === m.value}
                className={mode === m.value ? "active" : ""}
                title={m.hint}
                onClick={() => setMode(m.value)}
                disabled={!!live}
              >
                {m.label}
              </button>
            ))}
          </div>
        </div>

        {error ? <p className="alert alert-error">{error}</p> : null}
        {detail?.stale && detail.conversation.id === activeId ? (
          <p className="alert alert-warn">
            This project was re-analyzed after some answers below were written. Earlier answers may describe older code;
            new questions retrieve fresh evidence.
          </p>
        ) : null}

        <div className="chat-thread">
          {messages.length === 0 && !live ? (
            <div className="empty chat-empty">
              Ask about this repository, e.g. “How does authentication work?” or “What happens when a ZIP archive is
              uploaded?”. Answers are built from indexed source code and cite the lines they rely on.
            </div>
          ) : null}
          {messages.map((m) => (
            <MessageView key={m.id} message={m} onOpen={onOpen} />
          ))}
          {live ? <LiveView live={live} /> : null}
          <div ref={bottom} />
        </div>

        <form className="chat-input card" onSubmit={send}>
          <textarea
            value={input}
            maxLength={2000}
            rows={3}
            placeholder={ready ? "Ask about this codebase…" : "The chat model is not available (see above)."}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send();
              }
            }}
            aria-label="Question"
          />
          <div className="chat-input-row">
            <span className="muted small">Enter to send · Shift+Enter for a new line</span>
            {live ? (
              <button type="button" className="btn btn-danger" onClick={stop}>
                Stop
              </button>
            ) : (
              <button className="btn btn-primary" type="submit" disabled={!ready || !input.trim()}>
                Send
              </button>
            )}
          </div>
        </form>
      </section>
    </div>
  );
}

function ModelStatus({ status, error, onRetry }: { status: LLMStatus | null; error: string | null; onRetry: () => void }) {
  if (error) {
    return (
      <span className="model-status down" title={error}>
        <span className="dot" /> {error} <button className="link-btn" onClick={onRetry}>Retry</button>
      </span>
    );
  }
  if (!status) return <span className="model-status"><span className="dot" /> Checking model…</span>;
  return (
    <span className={`model-status ${status.available ? "up" : "down"}`} title={status.message}>
      <span className="dot" />
      {status.available ? (
        <>
          <strong className="mono">{status.model}</strong>
          <span className="muted small">
            {status.provider}
            {status.sends_code_off_machine ? " · sends code off this machine" : " · runs locally"}
          </span>
        </>
      ) : (
        <>
          <span>{status.message}</span>
          <button className="link-btn" onClick={onRetry}>Retry</button>
        </>
      )}
    </span>
  );
}

function LiveView({ live }: { live: Live }) {
  return (
    <>
      <div className="msg msg-user">
        <p>{live.question}</p>
      </div>
      <div className="msg msg-assistant">
        {live.steps.length ? (
          <ul className="steps">
            {live.steps.map((s) => (
              <li key={s.key} className={`step step-${s.state}`}>
                {s.state === "running" ? <span className="spinner" /> : <span className="step-mark">{s.state === "done" ? "✓" : "!"}</span>}
                <span>{s.label}</span>
                {s.detail ? <span className="muted small">{s.detail}</span> : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small"><span className="spinner" /> Starting…</p>
        )}
        {live.text ? (
          <div className="answer">
            <Markdown text={live.text} citations={[]} />
          </div>
        ) : null}
        {live.stopped ? <p className="alert alert-warn">Stopped. The partial answer above is not saved.</p> : null}
        {live.error && !live.stopped ? <p className="alert alert-error">{live.error}</p> : null}
        {!live.error && !live.stopped && live.text ? (
          <p className="muted small">Citations are verified when the answer is complete.</p>
        ) : null}
      </div>
    </>
  );
}

function MessageView({ message, onOpen }: { message: ChatMessage; onOpen: OpenSource }) {
  const [copied, setCopied] = useState(false);
  if (message.role === "user") {
    return (
      <div className="msg msg-user">
        <p>{message.content}</p>
      </div>
    );
  }
  if (message.status === "generating") {
    return (
      <div className="msg msg-assistant">
        <p className="muted small"><span className="spinner" /> This answer is still being finalized…</p>
      </div>
    );
  }
  if (message.status !== "completed") {
    return (
      <div className="msg msg-assistant">
        <p className={`alert ${message.status === "cancelled" ? "alert-warn" : "alert-error"}`}>
          {message.status === "cancelled" ? "Answer stopped before it was complete; nothing was saved." : "The answer failed"}
          {message.error && message.status !== "cancelled" ? `: ${message.error}` : ""}
        </p>
      </div>
    );
  }
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(message.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard unavailable (e.g. insecure context): ignore
    }
  };
  return (
    <div className="msg msg-assistant">
      <div className="answer">
        <Markdown text={message.content} citations={message.citations} onOpen={onOpen} />
      </div>
      {message.citations.length ? (
        <div className="citations">
          <span className="muted small">Sources</span>
          {message.citations.map((c) => (
            <button
              key={c.evidence_id}
              className="citation-ref mono"
              onClick={() => onOpen(c.file_path, { start: c.start_line, end: c.end_line })}
              title={`Open ${c.reference}${c.symbol ? ` (${c.symbol})` : ""}`}
            >
              <span className="cite-id">{c.evidence_id}</span> {c.reference}
            </button>
          ))}
        </div>
      ) : (
        <p className="muted small">This answer cites no verified source lines.</p>
      )}
      {message.warnings.length ? (
        <details className="warnings">
          <summary className="small">{message.warnings.length} note{message.warnings.length === 1 ? "" : "s"}</summary>
          <ul>
            {message.warnings.map((w, i) => (
              <li key={i} className="small">{w}</li>
            ))}
          </ul>
        </details>
      ) : null}
      <div className="msg-foot muted small">
        <span>
          {message.mode === "investigation" ? "Investigation" : "Quick answer"} · {message.model ?? "?"}
          {message.run?.duration_ms != null ? ` · ${(message.run.duration_ms / 1000).toFixed(1)} s` : ""}
          {message.input_tokens != null ? ` · ${message.input_tokens} in / ${message.output_tokens ?? "?"} out tokens` : ""}
        </span>
        <button className="link-btn small" onClick={copy}>{copied ? "Copied" : "Copy answer"}</button>
      </div>
      {message.run ? <Activity run={message.run} /> : null}
    </div>
  );
}

function Activity({ run }: { run: NonNullable<ChatMessage["run"]> }) {
  const tools = run.trace.filter((t) => typeof t.tool === "string" && (t.step as number) > 0);
  return (
    <details className="activity">
      <summary className="small">
        Investigation activity · {run.tool_call_count} tool call{run.tool_call_count === 1 ? "" : "s"} ·{" "}
        {run.llm_request_count} model request{run.llm_request_count === 1 ? "" : "s"} · {run.evidence_count} evidence
        block{run.evidence_count === 1 ? "" : "s"}
        {run.planner ? ` · ${run.planner} planner` : ""}
      </summary>
      <ol className="activity-list">
        {run.trace.map((t, i) => (
          <TraceLine key={i} step={t} />
        ))}
      </ol>
      {tools.length === 0 && run.mode === "investigation" ? (
        <p className="muted small">No additional tools were needed beyond the initial retrieval.</p>
      ) : null}
    </details>
  );
}

const VERDICT_MARK: Record<string, string> = { verified: "✓", inferred: "≈", unverified: "?", unsupported: "✕" };

function TraceLine({ step }: { step: TraceStep }) {
  if (step.step === "relationship_check" && Array.isArray(step.checks)) {
    const checks = step.checks as { subject: string; relation: string; target: string; verdict: string; reason: string }[];
    return (
      <li className="small">
        Relationship claims checked against the parsed source:
        <ul className="relation-list">
          {checks.map((c, i) => (
            <li key={i} className={c.verdict === "unsupported" ? "trace-failed" : c.verdict === "verified" ? "" : "muted"}>
              {VERDICT_MARK[c.verdict] ?? "?"} <code>{c.subject}</code> {c.relation} <code>{c.target}</code>: {c.verdict} ({c.reason})
            </li>
          ))}
        </ul>
      </li>
    );
  }
  if (typeof step.note === "string") return <li className="small muted">{step.note}</li>;
  if (typeof step.stop_reason === "string") return <li className="small muted">Stopped: {step.stop_reason}</li>;
  if (step.tool === "initial_retrieval" || step.step === "retrieval") {
    const ev = (step.evidence_added ?? step.evidence) as string[] | undefined;
    return <li className="small">Initial retrieval · {ev?.length ?? 0} source block(s)</li>;
  }
  if (typeof step.tool === "string") {
    return (
      <li className={`small ${step.ok ? "" : "trace-failed"}`}>
        {step.ok ? "✓" : "✕"} {String(step.summary ?? step.tool)}
        {Array.isArray(step.evidence_added) && step.evidence_added.length ? (
          <span className="muted"> → {(step.evidence_added as string[]).join(", ")}</span>
        ) : null}
      </li>
    );
  }
  if (step.model_request) return <li className="small muted">Planning request ({String(step.tool_calls_returned)} tool call(s) returned)</li>;
  return null;
}

// ---- minimal, safe Markdown (no HTML passthrough) ----------------------------------------------

const INLINE = /(`[^`\n]+`|\*\*[^*\n]+\*\*|\[\s*E\d+(?:\s*[,;]\s*E\d+)*\s*\])/g;

function Inline({ text, cites, onOpen }: { text: string; cites: Map<string, MessageCitation>; onOpen?: OpenSource }) {
  const parts = text.split(INLINE);
  return (
    <>
      {parts.map((part, i) => {
        if (!part) return null;
        if (part.startsWith("`") && part.endsWith("`") && part.length > 1) return <code key={i}>{part.slice(1, -1)}</code>;
        if (part.startsWith("**") && part.endsWith("**")) return <strong key={i}>{part.slice(2, -2)}</strong>;
        const ids = /^\[\s*E\d+/.test(part) ? part.match(/E\d+/g) : null;
        if (ids) {
          return (
            <Fragment key={i}>
              {ids.map((id) => {
                const c = cites.get(id);
                return c && onOpen ? (
                  <button
                    key={id}
                    className="cite"
                    onClick={() => onOpen(c.file_path, { start: c.start_line, end: c.end_line })}
                    title={`Open ${c.reference}`}
                  >
                    {id}
                  </button>
                ) : (
                  <span key={id} className="cite cite-pending" title="Not verified yet">{id}</span>
                );
              })}
            </Fragment>
          );
        }
        return <Fragment key={i}>{part}</Fragment>;
      })}
    </>
  );
}

function CodeBlock({ code, lang }: { code: string; lang: string }) {
  const html = useMemo(() => (lang ? highlight(code, lang) : null), [code, lang]);
  return html !== null ? (
    // highlight.js escapes all source text, so this HTML contains only its own spans.
    <pre className="md-code hljs"><code dangerouslySetInnerHTML={{ __html: html }} /></pre>
  ) : (
    <pre className="md-code"><code>{code}</code></pre>
  );
}

function Markdown({ text, citations, onOpen }: { text: string; citations: MessageCitation[]; onOpen?: OpenSource }) {
  const cites = useMemo(() => new Map(citations.map((c) => [c.evidence_id, c])), [citations]);
  const nodes: ReactNode[] = [];
  const segments = text.split(/```/);
  segments.forEach((segment, si) => {
    if (si % 2 === 1) {
      const newline = segment.indexOf("\n");
      const lang = newline >= 0 ? segment.slice(0, newline).trim() : "";
      const code = newline >= 0 ? segment.slice(newline + 1) : segment;
      nodes.push(<CodeBlock key={`c${si}`} code={code.replace(/\n$/, "")} lang={lang} />);
      return;
    }
    const blocks = segment.split(/\n{2,}/);
    blocks.forEach((block, bi) => {
      const lines = block.split("\n").filter((l) => l.trim());
      if (!lines.length) return;
      const key = `b${si}-${bi}`;
      if (lines.every((l) => /^\s*([-*]|\d+[.)])\s+/.test(l))) {
        const ordered = /^\s*\d/.test(lines[0]);
        const items = lines.map((l, li) => (
          <li key={li}><Inline text={l.replace(/^\s*([-*]|\d+[.)])\s+/, "")} cites={cites} onOpen={onOpen} /></li>
        ));
        nodes.push(ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>);
        return;
      }
      lines.forEach((line, li) => {
        const heading = line.match(/^(#{1,6})\s+(.*)$/);
        if (heading) {
          nodes.push(<p key={`${key}-${li}`} className="md-heading"><Inline text={heading[2]} cites={cites} onOpen={onOpen} /></p>);
        } else if (/^\s*([-*]|\d+[.)])\s+/.test(line)) {
          nodes.push(<ul key={`${key}-${li}`}><li><Inline text={line.replace(/^\s*([-*]|\d+[.)])\s+/, "")} cites={cites} onOpen={onOpen} /></li></ul>);
        } else {
          nodes.push(<p key={`${key}-${li}`}><Inline text={line} cites={cites} onOpen={onOpen} /></p>);
        }
      });
    });
  });
  return <>{nodes}</>;
}
