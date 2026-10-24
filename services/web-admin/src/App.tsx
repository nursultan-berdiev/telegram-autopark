import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./lib/auth";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Cars from "./pages/Cars";
import CarDetail from "./pages/CarDetail";
import Drivers from "./pages/Drivers";
import DriverDetail from "./pages/DriverDetail";
import Announcements from "./pages/Announcements";
import Periodic from "./pages/Periodic";
import Documents from "./pages/Documents";
import Reports from "./pages/Reports";
import Journals from "./pages/Journals";

function Gate() {
  const { me, loading } = useAuth();
  if (loading) return <div className="center muted">Загрузка…</div>;
  if (!me) return <Login />;
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Dashboard />} />
        <Route path="cars" element={<Cars />} />
        <Route path="cars/:id" element={<CarDetail />} />
        <Route path="drivers" element={<Drivers />} />
        <Route path="drivers/:id" element={<DriverDetail />} />
        <Route path="announcements" element={<Announcements />} />
        <Route path="periodic" element={<Periodic />} />
        <Route path="documents" element={<Documents />} />
        <Route path="reports" element={<Reports />} />
        <Route path="journals" element={<Journals />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Gate />
      </BrowserRouter>
    </AuthProvider>
  );
}
