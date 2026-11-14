import { useApi } from "../../lib/hooks";
import { api } from "../../lib/api";
import { money } from "../../lib/carStatus";
import { ActionButton, Async, fmtDate } from "../ui";
import type { Fine } from "../../lib/types";

export default function FinesTab({ carId }: { carId: string }) {
  const fines = useApi<Fine[]>(`/cars/${carId}/fines`);
  return (
    <Async loading={fines.loading} error={fines.error} data={fines.data} empty="Штрафов нет">
      {(rows) => (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Сумма</th><th>Выписан</th><th>Статус</th><th>Оплачен</th><th></th></tr></thead>
            <tbody>
              {rows.map((f) => (
                <tr key={f.id} className={f.paid_at ? "" : "overdue"}>
                  <td>{money(f.amount_to_pay ?? f.amount)}</td>
                  <td>{fmtDate(f.issued_at)}</td>
                  <td>{f.status === "paid" ? "оплачен" : "не оплачен"}</td>
                  <td>{f.paid_at ? fmtDate(f.paid_at) : "нет"}</td>
                  <td className="row-actions">
                    {!f.paid_at && (
                      <ActionButton label="Оплачен" onAction={() => api.post(`/fines/${f.id}/pay`)} onDone={fines.reload} />
                    )}
                    <ActionButton
                      label="Удалить"
                      confirm="Удалить штраф?"
                      danger
                      onAction={() => api.del(`/fines/${f.id}`)}
                      onDone={fines.reload}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Async>
  );
}
