import { Card } from "../components/ui";
import DocumentList from "../components/DocumentList";

// Вкладка «Общие документы» в настройках: видны всем водителям. Документы
// конкретной машины — на её странице. Загрузка — через бота.
export default function Documents() {
  return (
    <>
      <Card title="Общие документы парка">
        <p className="muted hint" style={{ marginTop: 0 }}>
          Загрузка документов — через бота. Здесь можно просмотреть и удалить. Документы конкретной
          машины — на её карточке.
        </p>
        <DocumentList path="/documents/common" />
      </Card>
    </>
  );
}
