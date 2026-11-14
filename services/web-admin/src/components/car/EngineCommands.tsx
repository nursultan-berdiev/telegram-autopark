import { api } from "../../lib/api";
import { ActionButton, Async, Card, fmtDate } from "../ui";
import type { Command, CommandResult } from "../../lib/types";

// Журнал команд двигателя машины; взведённую блокировку можно отменить.
export default function EngineCommands({
  carId,
  commands,
  onChanged,
}: {
  carId: string;
  commands: { data: Command[] | null; error: string | null; loading: boolean };
  onChanged: () => void;
}) {
  const cancelArmed = async (cid: number) => {
    // Команда могла уйти из armed (гонка с fire_armed) — тогда ok:false и
    // двигатель фактически заблокирован; молча «успех» показывать нельзя.
    const r = await api.post<CommandResult>(`/cars/${carId}/commands/${cid}/cancel`);
    if (!r.ok) throw new Error(r.reason ?? "не удалось отменить — проверьте статус");
  };

  return (
    <Card title="Команды двигателя">
      <Async loading={commands.loading} error={commands.error} data={commands.data} empty="Команд не было">
        {(rows) => (
          <div className="table-wrap">
            <table>
              <thead><tr><th>Тип</th><th>Статус</th><th>Источник</th><th>Когда</th><th></th></tr></thead>
              <tbody>
                {rows.map((c) => (
                  <tr key={c.id}>
                    <td>{c.type}</td>
                    <td>{c.status}</td>
                    <td>{c.source ?? "—"}</td>
                    <td>{fmtDate(c.created_at)}</td>
                    <td className="row-actions">
                      {c.status === "armed" && (
                        <ActionButton
                          label="Отменить ожидание"
                          confirm="Отменить взведённую блокировку?"
                          onAction={() => cancelArmed(c.id)}
                          onDone={onChanged}
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Async>
    </Card>
  );
}
