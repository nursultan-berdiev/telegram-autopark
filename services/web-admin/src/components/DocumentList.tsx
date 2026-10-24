import { useApi } from "../lib/hooks";
import { api } from "../lib/api";
import { ActionButton, Async, fmtDate } from "./ui";
import { MediaImage } from "./Media";
import type { Document } from "../lib/types";

function fmtSize(bytes: number | null): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} Б`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`;
  return `${(bytes / 1024 / 1024).toFixed(1)} МБ`;
}

// Эскиз показываем только для того, что медиа-прокси отдаёт inline-картинкой
// (jpeg/png/gif): фото из Telegram или файл-документ с картиночным расширением.
// Иначе (PDF и прочее) не тянем байты зря — рисуем иконку «файл».
function isImage(d: Document): boolean {
  if (d.file_kind === "photo") return true;
  return /\.(jpe?g|png|gif)$/i.test(d.file_name ?? "");
}

// Список документов из любого источника (общие парка или конкретной машины).
// Загрузка — через бота; здесь только просмотр и удаление.
export default function DocumentList({ path }: { path: string }) {
  const { data, error, loading, reload } = useApi<Document[]>(path);
  return (
    <Async loading={loading} error={error} data={data} empty="Документов нет">
      {(rows) => (
        <div className="doc-list">
          {rows.map((d) => {
            const href = `/api/media/document/${d.id}`;
            return (
              <div className="doc" key={d.id}>
                {isImage(d) ? (
                  <MediaImage
                    kind="document"
                    refId={d.id}
                    alt={d.title}
                    className="doc-thumb"
                    fallback={<span className="doc-ico">файл</span>}
                  />
                ) : (
                  <span className="doc-ico">файл</span>
                )}
                <div className="doc-main">
                  <div className="doc-title">{d.title}</div>
                  <div className="doc-sub">
                    {d.file_name ?? (d.file_kind === "photo" ? "фото" : "файл")}
                    {d.file_size ? ` · ${fmtSize(d.file_size)}` : ""} · {fmtDate(d.created_at)}
                  </div>
                </div>
                <div className="doc-actions">
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    Открыть
                  </a>
                  <ActionButton
                    label="Удалить"
                    confirm="Удалить документ?"
                    danger
                    onAction={() => api.del(`/documents/${d.id}`)}
                    onDone={reload}
                  />
                </div>
              </div>
            );
          })}
        </div>
      )}
    </Async>
  );
}
