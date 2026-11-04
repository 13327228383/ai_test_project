import { BrowserRouter as Router, Routes, Route } from "react-router-dom";
import LogPanel from "@/pages/LogPanel";

export default function App() {
  return (
    <Router basename="/log-panel">
      <Routes>
        <Route path="/" element={<LogPanel />} />
      </Routes>
    </Router>
  );
}
