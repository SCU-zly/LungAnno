import "./sab-polyfill"; // 必须最先执行：为后续模块提供 SharedArrayBuffer 兜底
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode><App /></React.StrictMode>
);
