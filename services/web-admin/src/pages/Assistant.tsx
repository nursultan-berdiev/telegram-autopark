import { useState } from "react";
import { api, ApiError } from "../lib/api";
import { Card } from "../components/ui";
import type { AssistantAnswer } from "../lib/types";

// Понятный текст вместо сырого сообщения («Unauthorized», «Failed to fetch»).
function errText(e: unknown): string {
  if (e instanceof ApiError) {
    if (e.status === 401 || e.status === 403) return "Нет доступа к ассистенту.";
    if (e.status >= 500) return "Ассистент временно недоступен, попробуйте позже.";
    return e.message; // осмысленные 4xx (например, валидация) показываем как есть
  }
  return "Не удалось связаться с сервером.";
}

// ВНИМАНИЕ: бэкенд /assistant/query ходит в ИИ. Пока владелец не выбрал backend
// (подписка через gateway / CLI / платный ключ), вызов на dev отвечает 502 —
// интерфейс это честно показывает, платные токены не тратятся.
export default function Assistant() {
  const [q, setQ] = useState("");
  const [answer, setAnswer] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ask = async () => {
    // Ctrl+Enter в textarea зовёт ask() в обход disabled-кнопки — без этой
    // проверки повторное нажатие слало бы второй ИИ-вызов, а ответы могли
    // прийти не по порядку. Один запрос за раз.
    if (busy) return;
    const question = q.trim();
    if (!question) return;
    setBusy(true);
    setErr(null);
    setAnswer(null); // убрать прежний ответ, пока идёт новый запрос / при ошибке
    try {
      const res = await api.post<AssistantAnswer>("/assistant/query", { question });
      setAnswer(res.answer);
    } catch (e) {
      setErr(errText(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Ассистент</h1>
      <Card title="Вопрос по автопарку">
        <p className="muted hint" style={{ marginTop: 0 }}>
          Задайте вопрос на русском — ассистент ответит по текущим данным парка (кто за рулём,
          долги и просрочка, штрафы, тревоги).
        </p>
        <textarea
          className="field"
          rows={3}
          value={q}
          placeholder="Например: у кого просрочка оплаты?"
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
              e.preventDefault();
              ask();
            }
          }}
        />
        <div className="actions-row">
          <button onClick={ask} disabled={busy || !q.trim()}>
            {busy ? "Думаю…" : "Спросить"}
          </button>
          <span className="muted" style={{ fontSize: 13 }}>
            Ctrl+Enter — отправить
          </span>
          {err && <span className="error">{err}</span>}
        </div>
      </Card>

      {answer != null && (
        <Card title="Ответ">
          <div className="answer">{answer}</div>
        </Card>
      )}
    </>
  );
}
