import { Card } from "../components/ui";
import DocumentList from "../components/DocumentList";

// Общие документы парка (видны всем водителям). Документы конкретной машины —
// на её карточке (страница машины). Загрузка — через бота.
export default function Documents() {
  return (
    <>
      <h1>Документы</h1>
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
