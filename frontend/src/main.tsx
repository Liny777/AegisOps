import "@fontsource-variable/geist"; // 32 号规范：字体族 Geist Variable，fallback Inter
import "@fontsource-variable/inter";
import "@tabler/icons-webfont/dist/tabler-icons.css";
import "./theme/global.css";
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
