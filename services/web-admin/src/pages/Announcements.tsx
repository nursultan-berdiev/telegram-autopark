import { useState } from "react";
import { api } from "../lib/api";
import { useApi } from "../lib/hooks";
import { ActionButton, Async, Card, fmtDate } from "../components/ui";
import type { Announcement } from "../lib/types";

// Лимит из контракта ANNOUNCEMENT_MAX_TEXT. Telegram меряет длину в UTF-16 code
// units — ровно то, что считает String.length в JS, поэтому text.length здесь
// совпадает с серверной telegram_length().
const MAX = 3500;

export default function Announcements() {
  const { data, error, loading, reload } = useApi<Announcement[]>("/announcements");
  const [text, setText] = useState("");
  const trimmed = text.trim();
  const over = text.length > MAX;
  const canSend = trimmed.length > 0 && !over;

  const publish = async () => {
    const dto = await api.post<Announcement>("/announcements", { text: trimmed });
    setText("");
    return `Разослано водителям: ${dto.recipients}`;
  };

  return (
    <>
      <h1>Объявления</h1>

      <Card title="Новое объявление">
        <textarea
          className="field"
          rows={5}
          value={text}
          placeholder="Текст объявления — придёт всем активным водителям"
          onChange={(e) => setText(e.target.value)}
        />
        <div className="field-counter">
          <span className={over ? "over" : ""}>{text.length}</span> / {MAX}
        </div>
        {trimmed && (
          <div className="preview">
            <b>📢 Объявление автопарка</b>
            <div className="preview-body">{trimmed}</div>
          </div>
        )}
        <div className="actions-row">
          {canSend ? (
            <ActionButton
              label="Разослать"
              confirm="Разослать объявление всем активным водителям?"
              onAction={publish}
              onDone={reload}
            />
          ) : (
            <button disabled>Разослать</button>
          )}
        </div>
      </Card>

      <Card title="История">
        <Async loading={loading} error={error} data={data} empty="Объявлений не было">
          {(rows) => (
            <table>
              <thead>
                <tr><th>Когда</th><th>Текст</th><th>Получателей</th><th></th></tr>
              </thead>
              <tbody>
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td>{fmtDate(a.created_at)}</td>
                    <td className="wrap">{a.text}</td>
                    <td>{a.recipients}</td>
                    <td className="row-actions">
                      <ActionButton
                        label="Удалить"
                        confirm="Удалить объявление из истории?"
                        danger
                        onAction={() => api.del(`/announcements/${a.id}`)}
                        onDone={reload}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Async>
      </Card>
    </>
  );
}
