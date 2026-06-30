import { Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./contexts/AuthContext";
import Layout from "./components/Layout";
import CrossRefTable from "./components/CrossRefTable";
import DocumentDetail from "./components/DocumentDetail";
import GraphVisualization from "./components/GraphVisualization";
import LoginPage from "./components/LoginPage";
import SearchPage from "./components/SearchPage";
import type { ReactNode } from "react";

function ProtectedRoute({ children }: { children: ReactNode }) {
  const { user, isLoading } = useAuth();
  if (isLoading) return <div className="flex items-center justify-center min-h-screen text-gray-500">Chargement...</div>;
  if (!user) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="*"
        element={
          <ProtectedRoute>
            <Layout>
              <Routes>
                <Route path="/" element={<SearchPage />} />
                <Route path="/graph" element={<GraphVisualization />} />
                <Route path="/crossrefs" element={<CrossRefTable />} />
                <Route path="/documents/:sourceType/:docId" element={<DocumentDetail />} />
              </Routes>
            </Layout>
          </ProtectedRoute>
        }
      />
    </Routes>
  );
}
