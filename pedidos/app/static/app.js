"use strict";
const $ = (s, el = document) => el.querySelector(s);
const API = "api";
let busqueda = null;      // id de la búsqueda activa en aMule
let resultados = [];
let filtro = "todos";
let verTodos = false;

const ETIQUETAS = {
  esperando: "En cola", descargando: "Descargando", importando: "Añadiendo a la biblioteca",
  en_biblioteca: "En la biblioteca", pausado: "Pausado", cancelado: "Cancelado",
  error: "Error", perdido: "No encontrado", desconocido: "aMule no responde",
};

async function api(ruta, opciones = {}) {
  const r = await fetch(`${API}/${ruta}`, {
    headers: { "Content-Type": "application/json" }, credentials: "same-origin", ...opciones,
  });
  const datos = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(datos.error || `Error ${r.status}`);
  return datos;
}

function avisar(texto, mal = false) {
  const a = $("#aviso");
  a.textContent = texto;
  a.className = "aviso" + (mal ? " mal" : "");
  a.hidden = false;
  clearTimeout(avisar.t);
  avisar.t = setTimeout(() => (a.hidden = true), 3500);
}

const tamano = (b) => {
  if (!b) return "";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
  return `${b.toFixed(b < 10 && i ? 1 : 0)} ${u[i]}`;
};
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const limpio = (n) => n.replace(/\.[^.]+$/, "").replace(/[._]+/g, " ").replace(/\s+/g, " ").trim();
const hace = (t) => {
  const s = Math.round(Date.now() / 1000 - t);
  if (s < 60) return "ahora";
  if (s < 3600) return `hace ${Math.round(s / 60)} min`;
  if (s < 86400) return `hace ${Math.round(s / 3600)} h`;
  return new Date(t * 1000).toLocaleDateString("es-ES", { day: "numeric", month: "short" });
};
const calidad = (completas) => {
  const n = completas >= 20 ? 4 : completas >= 8 ? 3 : completas >= 3 ? 2 : completas >= 1 ? 1 : 0;
  return `<span class="calidad" title="Disponibilidad">${[0, 1, 2, 3].map((i) => `<i class="${i < n ? "on" : ""}"></i>`).join("")}</span>`;
};

// ------------------------------------------------------------ búsqueda
$("#buscar").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#q").value.trim();
  if (q.length < 3) return;
  const boton = $("#buscar button");
  boton.disabled = true;
  if (busqueda) api(`busquedas/${busqueda}`, { method: "DELETE" }).catch(() => {});
  resultados = [];
  filtro = "todos";
  pintarResultados();
  $("#resultados").hidden = false;
  $("#vacio").hidden = true;
  const barra = $("#progreso");
  barra.hidden = false;
  barra.firstElementChild.style.width = "3%";
  try {
    const { id } = await api("busquedas", { method: "POST", body: JSON.stringify({ q }) });
    busqueda = id;
    await sondear(id);
  } catch (err) {
    avisar(err.message, true);
  } finally {
    boton.disabled = false;
    barra.hidden = true;
  }
});

async function sondear(id) {
  for (let i = 0; i < 120 && busqueda === id; i++) {
    const d = await api(`busquedas/${id}`);
    resultados = d.resultados;
    $("#progreso").firstElementChild.style.width = `${Math.max(3, d.porcentaje)}%`;
    pintarResultados();
    if (d.estado !== "running") break;
    await new Promise((r) => setTimeout(r, 2000));
  }
  $("#vacio").hidden = resultados.length > 0;
}

function pintarResultados() {
  const formatos = [...new Set(resultados.map((r) => r.formato))];
  $("#filtros").innerHTML = formatos.length > 1
    ? ["todos", ...formatos].map((f) => `<button class="${f === filtro ? "activa" : ""}" data-f="${esc(f)}">${f === "todos" ? "Todos" : esc(f.toUpperCase())}</button>`).join("")
    : "";
  const visibles = resultados.filter((r) => filtro === "todos" || r.formato === filtro);
  $("#lista").innerHTML = visibles.map((r) => `
    <li class="item">
      <div class="formato" data-f="${esc(r.formato)}">${esc(r.formato)}</div>
      <div>
        <div class="titulo">${esc(limpio(r.nombre))}</div>
        <div class="meta">
          <span>${tamano(r.tamano)}</span>
          <span class="fuentes">${calidad(r.completas)}<b>${r.completas}</b> completas · ${r.fuentes} fuentes</span>
        </div>
      </div>
      <div class="acciones">
        ${r.pedido
          ? `<span class="estado">Ya pedido</span>`
          : `<button class="boton" data-pedir="${esc(r.hash)}">Pedir</button>`}
      </div>
    </li>`).join("");
}

$("#filtros").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-f]");
  if (!b) return;
  filtro = b.dataset.f;
  pintarResultados();
});

$("#lista").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-pedir]");
  if (!b) return;
  const r = resultados.find((x) => x.hash === b.dataset.pedir);
  b.disabled = true;
  try {
    await api("pedidos", { method: "POST", body: JSON.stringify({ hash: r.hash, nombre: r.nombre, tamano: r.tamano, ecid: r.ecid }) });
    r.pedido = true;
    pintarResultados();
    avisar("Pedido hecho. Te avisamos aquí cuando esté en la biblioteca.");
    cargarPedidos();
  } catch (err) {
    b.disabled = false;
    avisar(err.message, true);
  }
});

// ------------------------------------------------------------ pedidos
async function cargarPedidos() {
  try {
    const d = await api(`pedidos?todos=${verTodos}`);
    $("#sin-pedidos").hidden = d.pedidos.length > 0;
    $("#pedidos").innerHTML = d.pedidos.map((p) => {
      const activo = ["descargando", "esperando", "importando", "pausado"].includes(p.estado);
      const fmt = (p.nombre.split(".").pop() || "").toLowerCase();
      return `
      <li class="item">
        <div class="formato" data-f="${esc(fmt)}">${esc(fmt)}</div>
        <div>
          <div class="titulo">${esc(limpio(p.nombre))}</div>
          <div class="meta">
            <span>${tamano(p.tamano)}</span>
            ${verTodos ? `<span>pedido por ${esc(p.nombre_usuario || p.usuario)}</span>` : ""}
            <span>${hace(p.creado)}</span>
            ${p.estado === "descargando" ? `<span>${tamano(p.velocidad)}/s · ${p.fuentes} fuentes</span>` : ""}
            ${p.estado === "esperando" && p.fuentes === 0 && Date.now() / 1000 - p.creado > 300 ? `<span>buscando fuentes… puede tardar horas</span>` : ""}
          </div>
          ${activo ? `<div class="barra-p"><div style="width:${p.porcentaje}%"></div></div>` : ""}
        </div>
        <div class="acciones">
          <span class="estado ${p.estado}">${ETIQUETAS[p.estado] || p.estado}${activo && p.porcentaje ? ` · ${Math.floor(p.porcentaje)}%` : ""}</span>
          ${p.enlace ? `<a class="icono" href="${esc(p.enlace)}">Abrir</a>` : ""}
          ${activo && p.propio ? `<button class="icono" data-cancelar="${esc(p.hash)}" title="Cancelar">✕</button>` : ""}
        </div>
      </li>`;
    }).join("");
  } catch (err) {
    console.warn(err);
  }
}

$(".pestanas").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  document.querySelectorAll(".pestanas button").forEach((x) => x.classList.toggle("activa", x === b));
  verTodos = b.dataset.todos === "1";
  cargarPedidos();
});

$("#pedidos").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-cancelar]");
  if (!b || !confirm("¿Cancelar este pedido?")) return;
  try {
    await api(`pedidos/${b.dataset.cancelar}`, { method: "DELETE" });
    cargarPedidos();
  } catch (err) {
    avisar(err.message, true);
  }
});

async function estadoRed() {
  const el = $("#red");
  try {
    const s = await api("estado");
    const ok = s.kad === "connected" || s.ed2k === "connected";
    el.className = "red " + (ok ? "ok" : "mal");
    el.querySelector("b").textContent = ok ? `conectado${s.ed2k === "connected" && !s.high_id ? " (LowID)" : ""}` : "sin red";
  } catch {
    el.className = "red mal";
    el.querySelector("b").textContent = "aMule caído";
  }
}

(async () => {
  try {
    const yo = await api("yo");
    $("#yo").textContent = yo.nombre;
  } catch { /* sin sesión: Authelia redirigirá */ }
  cargarPedidos();
  estadoRed();
  setInterval(cargarPedidos, 5000);
  setInterval(estadoRed, 30000);
})();
