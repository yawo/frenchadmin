import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import CrossRefTable from "./components/CrossRefTable";
import DocumentDetail from "./components/DocumentDetail";
import GraphVisualization from "./components/GraphVisualization";
import SearchPage from "./components/SearchPage";

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<SearchPage />} />
        <Route path="/graph" element={<GraphVisualization />} />
        <Route path="/crossrefs" element={<CrossRefTable />} />
        <Route path="/documents/:sourceType/:docId" element={<DocumentDetail />} />
      </Routes>
    </Layout>
  );
}
