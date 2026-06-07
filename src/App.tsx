import { Sparkles } from "lucide-react";
import { V2Ratings } from "./components/V2Ratings";

export default function App() {
  return (
    <div className="app-shell single-page-shell">
      <header className="topbar">
        <div className="brand compact-brand">
          <span className="brand-mark">
            <Sparkles size={22} />
          </span>
          <div>
            <strong>AI投委会 2.0</strong>
            <small>重点300公司质量 × 估值评级</small>
          </div>
        </div>
      </header>
      <div className="app-content">
        <V2Ratings />
      </div>
    </div>
  );
}
