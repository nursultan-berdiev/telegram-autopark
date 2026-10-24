export default function Login() {
  return (
    <div className="center">
      <div className="card login">
        <h2>Вход в админку</h2>
        <p className="muted">Вход — через Telegram-бота автопарка (пароля нет):</p>
        <ol>
          <li>Откройте бота и нажмите «⚙️ Админка».</li>
          <li>Перейдите по одноразовой ссылке (действует 15 минут).</li>
          <li>Вернитесь сюда и обновите страницу.</li>
        </ol>
        <button onClick={() => location.reload()}>Я вошёл — обновить</button>
      </div>
    </div>
  );
}
