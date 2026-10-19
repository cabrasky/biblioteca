/* [biblioteca] Pestaña «Peticiones» de Calibre-Web: buscar y pedir libros (API en data-api). */
(function () {
  "use strict";
  var raiz = document.getElementById("peticiones");
  if (!raiz) return;
  var API = raiz.getAttribute("data-api");
  var $ = function (s) { return document.querySelector(s); };
  var busqueda = null, resultados = [], filtro = "todos", verTodos = false;

  var ETIQUETAS = {
    esperando: ["En cola", "default"], descargando: ["Descargando", "info"], importando: ["Añadiendo…", "info"],
    en_biblioteca: ["En la biblioteca", "success"], pausado: ["Pausado", "warning"], cancelado: ["Cancelado", "default"],
    error: ["Error", "danger"], perdido: ["No encontrado", "danger"], desconocido: ["Sin conexión", "warning"]
  };

  function api(ruta, opciones) {
    opciones = opciones || {};
    opciones.headers = { "Content-Type": "application/json" };
    opciones.credentials = "same-origin";
    return fetch(API + "/" + ruta, opciones).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || ("Error " + r.status));
        return d;
      });
    });
  }
  function avisar(texto, mal) {
    var div = document.createElement("div");
    div.className = "alert alert-" + (mal ? "danger" : "success");
    div.style.cssText = "position:fixed;left:50%;bottom:20px;transform:translateX(-50%);z-index:2000;box-shadow:0 6px 20px rgba(0,0,0,.25)";
    div.textContent = texto;
    document.body.appendChild(div);
    setTimeout(function () { div.remove(); }, 3500);
  }
  function tamano(b) {
    if (!b) return "";
    var u = ["B", "KB", "MB", "GB"], i = 0;
    while (b >= 1024 && i < u.length - 1) { b /= 1024; i++; }
    return b.toFixed(b < 10 && i ? 1 : 0) + " " + u[i];
  }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function limpio(n) { return n.replace(/\.[^.]+$/, "").replace(/[._]+/g, " ").replace(/\s+/g, " ").trim(); }
  function hace(t) {
    var s = Math.round(Date.now() / 1000 - t);
    if (s < 60) return "ahora";
    if (s < 3600) return "hace " + Math.round(s / 60) + " min";
    if (s < 86400) return "hace " + Math.round(s / 3600) + " h";
    return new Date(t * 1000).toLocaleDateString("es-ES", { day: "numeric", month: "short" });
  }
  function calidad(c) {
    var n = c >= 20 ? 4 : c >= 8 ? 3 : c >= 3 ? 2 : c >= 1 ? 1 : 0, h = "";
    for (var i = 0; i < 4; i++) h += '<i class="' + (i < n ? "on" : "") + '"></i>';
    return '<span class="pet-calidad" title="Disponibilidad">' + h + "</span>";
  }
  function formato(f) { return '<span class="pet-formato f-' + esc(f) + '">' + esc(f) + "</span>"; }
  function espera(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  /* ---------------- eBiblio (préstamo gratuito, antes que eMule) ---------------- */
  var ESTADO_EB = { disponible: ["Disponible", "success"], reservable: ["Prestado · se puede reservar", "warning"],
                    no_disponible: ["Sin ejemplares ahora", "default"] };

  function pintarEbiblio(d) {
    var panel = $("#pet-ebiblio");
    if (!d.activo || !d.resultados.length) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.className = "pet-ebiblio" + (d.recomendar ? "" : " secundario");
    $("#pet-ebiblio-titulo").textContent = d.recomendar ? "Está en " + d.nombre + ": préstamo gratuito" : "Está en " + d.nombre + ", pero sin ejemplares ahora";
    $("#pet-ebiblio-texto").innerHTML = d.recomendar
      ? "Te recomendamos tomarlo prestado con tu carné de biblioteca antes de descargarlo. Si aun así lo quieres en la biblioteca, búscalo en eMule."
      : "No se puede prestar ni reservar en este momento, así que también lo buscamos en eMule.";
    $("#pet-ebiblio-lista").innerHTML = d.resultados.map(function (r) {
      var e = ESTADO_EB[r.estado] || [r.estado, "default"];
      return "<li>" + (r.portada ? '<img alt="" loading="lazy" src="' + esc(API + "/ebiblio/portada?u=" + encodeURIComponent(r.portada)) + '">' : "") +
        '<div class="pet-eb-info"><div class="pet-titulo">' + esc(r.titulo) + '</div><div class="pet-meta">' +
        esc(r.autores.join(", ")) + " · " + esc(r.tipo) + (r.formatos.length ? " · " + esc(r.formatos.join(", ")) : "") +
        " · " + esc(r.disponibilidad) + '</div></div><span class="label label-' + e[1] + '">' + e[0] + "</span> " +
        '<a class="btn btn-success btn-sm" target="_blank" rel="noopener" href="' + esc(r.url) + '">' +
        (r.estado === "disponible" ? "Prestar" : r.estado === "reservable" ? "Reservar" : "Ver") + " en " + esc(d.nombre) + "</a></li>";
    }).join("");
    $("#pet-ebiblio-acciones").hidden = !d.recomendar;
  }

  /* ---------------- búsqueda ---------------- */
  $("#pet-buscar").addEventListener("submit", function (e) {
    e.preventDefault();
    var q = $("#pet-q").value.trim();
    if (q.length < 3) return;
    var boton = $("#pet-buscar button");
    boton.disabled = true;
    $("#pet-resultados").hidden = true;
    $("#pet-ebiblio").hidden = true;
    // Primero eBiblio: si se puede prestar/reservar, se recomienda y NO se lanza eMule
    api("ebiblio?q=" + encodeURIComponent(q))
      .catch(function () { return { activo: false, resultados: [] }; })
      .then(function (d) {
        pintarEbiblio(d);
        if (d.recomendar) { boton.disabled = false; return; }
        buscarEmule(q, boton);
      });
  });

  $("#pet-emule-igual").addEventListener("click", function () {
    $("#pet-ebiblio-acciones").hidden = true;
    buscarEmule($("#pet-q").value.trim(), $("#pet-buscar button"));
  });

  function buscarEmule(q, boton) {
    boton.disabled = true;
    if (busqueda) api("busquedas/" + busqueda, { method: "DELETE" }).catch(function () {});
    resultados = []; filtro = "todos"; pintarResultados();
    $("#pet-resultados").hidden = false; $("#pet-vacio").hidden = true;
    var barra = $("#pet-progreso");
    barra.hidden = false; barra.firstElementChild.style.width = "3%";
    api("busquedas", { method: "POST", body: JSON.stringify({ q: q }) })
      .then(function (d) { busqueda = d.id; return sondear(d.id, 0); })
      .catch(function (err) { avisar(err.message, true); })
      .then(function () { boton.disabled = false; barra.hidden = true; });
  }

  function sondear(id, n) {
    if (busqueda !== id || n > 120) return Promise.resolve();
    return api("busquedas/" + id).then(function (d) {
      resultados = d.resultados;
      $("#pet-progreso").firstElementChild.style.width = Math.max(3, d.porcentaje) + "%";
      pintarResultados();
      if (d.estado === "running") return espera(2000).then(function () { return sondear(id, n + 1); });
      $("#pet-vacio").hidden = resultados.length > 0;
    });
  }

  function pintarResultados() {
    var formatos = [];
    resultados.forEach(function (r) { if (formatos.indexOf(r.formato) < 0) formatos.push(r.formato); });
    $("#pet-filtros").innerHTML = formatos.length > 1 ? ["todos"].concat(formatos).map(function (f) {
      return '<button type="button" class="btn btn-default' + (f === filtro ? " active" : "") + '" data-f="' + esc(f) + '">' +
        (f === "todos" ? "Todos" : esc(f.toUpperCase())) + "</button>";
    }).join("") : "";
    $("#pet-lista").innerHTML = resultados.filter(function (r) { return filtro === "todos" || r.formato === filtro; }).map(function (r) {
      return "<tr><td>" + formato(r.formato) + '</td><td><div class="pet-titulo">' + esc(limpio(r.nombre)) +
        '</div><div class="pet-meta">' + esc(r.nombre) + '</div></td><td class="text-right">' + tamano(r.tamano) +
        '</td><td class="text-right">' + calidad(r.completas) + r.completas + " / " + r.fuentes + '</td><td class="pet-acciones">' +
        (r.pedido ? '<span class="label label-default">Ya pedido</span>'
                  : '<button class="btn btn-primary btn-sm" data-pedir="' + esc(r.hash) + '"><span class="glyphicon glyphicon-download-alt"></span> Pedir</button>') +
        "</td></tr>";
    }).join("");
  }

  $("#pet-filtros").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-f]");
    if (!b) return;
    filtro = b.getAttribute("data-f"); pintarResultados();
  });

  $("#pet-lista").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-pedir]");
    if (!b) return;
    var h = b.getAttribute("data-pedir");
    var r = resultados.filter(function (x) { return x.hash === h; })[0];
    b.disabled = true;
    api("pedidos", { method: "POST", body: JSON.stringify({ hash: r.hash, nombre: r.nombre, tamano: r.tamano, ecid: r.ecid }) })
      .then(function () { r.pedido = true; pintarResultados(); avisar("Pedido hecho. Aparecerá en la biblioteca cuando termine."); cargarPedidos(); })
      .catch(function (err) { b.disabled = false; avisar(err.message, true); });
  });

  /* ---------------- pedidos ---------------- */
  function cargarPedidos() {
    return api("pedidos?todos=" + verTodos).then(function (d) {
      $("#pet-sin-pedidos").hidden = d.pedidos.length > 0;
      $("#pet-pedidos").innerHTML = d.pedidos.map(function (p) {
        var activo = ["descargando", "esperando", "importando", "pausado"].indexOf(p.estado) >= 0;
        var et = ETIQUETAS[p.estado] || [p.estado, "default"];
        var meta = [tamano(p.tamano), hace(p.creado)];
        if (verTodos) meta.push("pedido por " + esc(p.nombre_usuario || p.usuario));
        if (p.estado === "descargando") meta.push(tamano(p.velocidad) + "/s · " + p.fuentes + " fuentes");
        if (p.estado === "esperando" && !p.fuentes && Date.now() / 1000 - p.creado > 300) meta.push("buscando fuentes… puede tardar horas");
        var fmt = (p.nombre.split(".").pop() || "").toLowerCase();
        return "<tr><td>" + formato(fmt) + '</td><td><div class="pet-titulo">' + esc(limpio(p.nombre)) +
          '</div><div class="pet-meta">' + meta.join(" · ") + "</div>" +
          (activo ? '<div class="progress"><div class="progress-bar" style="width:' + p.porcentaje + '%"></div></div>' : "") +
          '</td><td><span class="label label-' + et[1] + '">' + et[0] + (activo && p.porcentaje ? " · " + Math.floor(p.porcentaje) + "%" : "") +
          '</span></td><td class="pet-acciones">' +
          (p.enlace ? '<a class="btn btn-default btn-sm" href="' + esc(p.enlace) + '"><span class="glyphicon glyphicon-book"></span> Abrir</a> ' : "") +
          (activo && p.propio ? '<button class="btn btn-default btn-sm" data-cancelar="' + esc(p.hash) + '" title="Cancelar"><span class="glyphicon glyphicon-remove"></span></button>' : "") +
          "</td></tr>";
      }).join("");
    }).catch(function (err) { console.warn(err); });
  }

  document.querySelector(".pet-pestanas").addEventListener("click", function (e) {
    var a = e.target.closest("a[data-todos]");
    if (!a) return;
    e.preventDefault();
    Array.prototype.forEach.call(document.querySelectorAll(".pet-pestanas li"), function (li) { li.classList.toggle("active", li === a.parentNode); });
    verTodos = a.getAttribute("data-todos") === "1";
    cargarPedidos();
  });

  $("#pet-pedidos").addEventListener("click", function (e) {
    var b = e.target.closest("button[data-cancelar]");
    if (!b || !confirm("¿Cancelar este pedido?")) return;
    api("pedidos/" + b.getAttribute("data-cancelar"), { method: "DELETE" })
      .then(cargarPedidos).catch(function (err) { avisar(err.message, true); });
  });

  function estadoRed() {
    var el = $("#pet-red");
    api("estado").then(function (s) {
      var ok = s.kad === "connected" || s.ed2k === "connected";
      el.className = "pet-red " + (ok ? "ok" : "mal");
      el.lastElementChild.textContent = ok ? "red conectada" + (s.ed2k === "connected" && !s.high_id ? " (LowID)" : "") : "sin red";
    }).catch(function () { el.className = "pet-red mal"; el.lastElementChild.textContent = "servicio caído"; });
  }

  cargarPedidos(); estadoRed();

  // ?q=... (p. ej. desde el buscador de arriba sin resultados): lanzar la búsqueda directamente
  var consulta = raiz.getAttribute("data-consulta");
  if (consulta && consulta.length >= 3) {
    $("#pet-q").value = consulta;
    $("#pet-buscar").dispatchEvent(new Event("submit", { cancelable: true }));
  }
  setInterval(cargarPedidos, 5000);
  setInterval(estadoRed, 30000);
})();
