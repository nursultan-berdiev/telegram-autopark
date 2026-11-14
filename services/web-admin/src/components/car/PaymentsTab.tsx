import { Link } from "react-router-dom";
import { useApi } from "../../lib/hooks";
import { money } from "../../lib/carStatus";
import { Async, fmtDate } from "../ui";
import type { CarPayment } from "../../lib/types";

// Оплаты по машине от любого водителя: при смене водителя история остаётся у машины.
export default function PaymentsTab({ carId }: { carId: string }) {
  const rows = useApi<CarPayment[]>(`/cars/${carId}/payments?limit=200`);
  return (
    <Async loading={rows.loading} error={rows.error} data={rows.data} empty="Оплат по этой машине не было">
      {(list) => (
        <div className="table-wrap">
          <table>
            <thead><tr><th>Когда оплачено</th><th>Сумма</th><th>Водитель</th><th>Чек принят</th></tr></thead>
            <tbody>
              {list.map((p) => (
                <tr key={p.id}>
                  <td>{fmtDate(p.paid_at ?? p.created_at)}</td>
                  <td>{money(p.amount)}</td>
                  <td>{p.driver_name ? <Link to={`/drivers/${p.driver_id}`}>{p.driver_name}</Link> : "—"}</td>
                  <td className="muted">{fmtDate(p.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Async>
  );
}
