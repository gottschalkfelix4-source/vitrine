import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./styles/tokens.css";
import "./styles/app.css";

const wurzel = document.getElementById("root");
if (!wurzel) throw new Error("#root fehlt in index.html");

createRoot(wurzel).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
