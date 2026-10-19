/* Menú «☰ Índice» común a las páginas de ZEIA (:8000).
 *
 * En cada página:
 *   <script src="/static/nav.js?v=1" defer></script>
 *   <span data-zeia-nav data-clase="btn"></span>    ← donde va el botón
 *
 * data-clase (opcional): clases de la página para que el botón tome su
 * estilo. Puede haber varios botones; todos abren el mismo panel lateral.
 * El listado sale de GET /api/indice (catálogo en src/indice.py) y se pide
 * al abrir. No se imprime ni sale en los PDF.
 */
(function () {
  "use strict";

  var MESES = ["ene", "feb", "mar", "abr", "may", "jun",
               "jul", "ago", "sep", "oct", "nov", "dic"];
  var ICONOS = {ia: "💬", vivo: "🟢", reportes: "📄", historico: "🔎", tecnico: "🛠"};
  var ICONO_BASE = {energia: "⚡", ambiental: "🌡"};
  var MODO = {produccion: "en vivo (producción)", local: "copia local", remota: "servidor remoto"};

  var CSS = [
    ".zeia-nav-btn{cursor:pointer;white-space:nowrap}",
    "dialog.zeia-nav{position:fixed;inset:0 auto 0 0;margin:0;padding:0;border:0;",
    "border-right:1px solid #2a3240;width:min(400px,92vw);max-width:92vw;height:100%;",
    "max-height:100%;background:#12161d;color:#e8ecf2;overflow:hidden;text-align:left;",
    "font:14px/1.45 system-ui,-apple-system,'Segoe UI',Roboto,sans-serif;",
    "box-shadow:12px 0 40px rgba(0,0,0,.45)}",
    "dialog.zeia-nav[open]{animation:zeia-nav-in .16s ease-out}",
    "dialog.zeia-nav::backdrop{background:rgba(5,8,12,.55)}",
    "@keyframes zeia-nav-in{from{transform:translateX(-20px);opacity:0}to{transform:none;opacity:1}}",
    "@media (prefers-reduced-motion:reduce){dialog.zeia-nav[open]{animation:none}}",
    ".zeia-nav *{box-sizing:border-box}",
    ".zeia-nav .zeia-nav-in{height:100%;overflow-y:auto;padding:14px 16px 22px}",
    ".zeia-nav .zeia-nav-cab{display:flex;align-items:center;justify-content:space-between;",
    "gap:10px;margin:0 0 12px}",
    ".zeia-nav .zeia-nav-cab b{font-size:15px}",
    ".zeia-nav .zeia-nav-x{background:transparent;border:1px solid #2f3846;color:#c9cfda;",
    "border-radius:8px;padding:3px 10px;margin:0;font:inherit;font-size:13px;cursor:pointer}",
    ".zeia-nav .zeia-nav-x:hover{border-color:#ffb454;color:#ffb454;background:transparent}",
    ".zeia-nav a{color:inherit;text-decoration:none}",
    ".zeia-nav a:focus-visible,.zeia-nav button:focus-visible{outline:2px solid #ffb454;outline-offset:1px}",
    ".zeia-nav .zeia-nav-inicio{display:block;border:1px solid #4a3b22;background:rgba(255,180,84,.08);",
    "border-radius:10px;padding:9px 12px;margin:0 0 6px}",
    ".zeia-nav .zeia-nav-inicio:hover{border-color:#ffb454}",
    ".zeia-nav .zeia-nav-inicio b{color:#ffb454}",
    ".zeia-nav small{display:block;color:#9aa3b2;font-size:12px;margin:1px 0 0;line-height:1.4}",
    ".zeia-nav h2.zeia-nav-sec{font-size:11.5px;font-weight:700;text-transform:uppercase;",
    "letter-spacing:.07em;color:#9aa3b2;margin:16px 0 6px;padding:0;border:0;background:none}",
    ".zeia-nav .zeia-nav-item{display:block;border-radius:9px;padding:7px 10px;margin:0 0 2px;",
    "border:1px solid transparent}",
    ".zeia-nav .zeia-nav-item:hover{background:#1a202a;border-color:#2a3240}",
    ".zeia-nav .zeia-nav-item[aria-current]{background:#1d2430;border-color:#ffb454}",
    ".zeia-nav .zeia-nav-t{font-weight:600}",
    ".zeia-nav .zeia-nav-r{font:11.5px ui-monospace,Consolas,monospace;color:#ffb454;margin-left:6px}",
    ".zeia-nav .zeia-nav-aqui{font-size:11px;font-weight:700;color:#0b0e13;background:#ffb454;",
    "border-radius:999px;padding:0 7px;margin-left:6px}",
    ".zeia-nav .zeia-nav-aviso{display:block;color:#f5b041;font-size:12px;margin-top:2px}",
    ".zeia-nav .zeia-nav-var{display:flex;flex-wrap:wrap;gap:5px;margin:2px 0 8px 10px;padding:0}",
    ".zeia-nav .zeia-nav-var a{font-size:12px;border:1px solid #2f3846;border-radius:999px;",
    "padding:1px 9px;color:#c9cfda}",
    ".zeia-nav .zeia-nav-var a:hover,.zeia-nav .zeia-nav-var a[aria-current]{border-color:#ffb454;color:#ffb454}",
    ".zeia-nav .zeia-nav-pie{border-top:1px solid #232a35;margin:14px 0 0;padding:10px 0 0;",
    "color:#9aa3b2;font-size:12px}",
    ".zeia-nav .zeia-nav-msg{color:#9aa3b2;font-size:13px;margin:10px 0}",
    "@media print{.zeia-nav-btn,dialog.zeia-nav{display:none!important}}"
  ].join("");

  var dialogo = null, lista = null, pie = null, botones = [], datos = null;

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c];
    });
  }

  function fecha(iso) {
    if (!iso) return "?";
    var p = String(iso).slice(0, 10).split("-");
    return p[2] + "-" + MESES[+p[1] - 1];
  }

  // ¿La URL es la página actual? (exacto = también los ?parámetros)
  function esActual(url, exacto) {
    try {
      var u = new URL(url, location.href);
      if (u.origin !== location.origin || u.pathname !== location.pathname) return false;
      return !exacto || u.search === location.search;
    } catch (e) {
      return false;
    }
  }

  // Aviso si la página lee un export (regenerable) que no cubre la semana pasada.
  function aviso(p, d) {
    var ids = (p.datos && p.datos.exports) || [];
    var malos = ids.map(function (id) { return d.exports[id]; })
      .filter(function (e) { return e && e.comando && e.estado !== "ok"; });
    if (!malos.length) return "";
    if (malos.some(function (e) { return e.estado === "falta"; })) {
      return "⚠ Falta el export de datos (ver Inicio)";
    }
    var hasta = malos.map(function (e) { return e.hasta; }).filter(Boolean).sort()[0];
    return "⚠ Datos hasta el " + fecha(hasta) + ": export sin actualizar";
  }

  function render(d) {
    var html = "";
    d.secciones.forEach(function (s) {
      if (!s.paginas.length) return;
      html += '<h2 class="zeia-nav-sec"><span aria-hidden="true">' + (ICONOS[s.id] || "•") +
              "</span> " + esc(s.titulo) + "</h2>";
      s.paginas.forEach(function (p) {
        var actual = esActual(p.url, false);
        var av = aviso(p, d);
        var ruta = p.ruta || (":" + p.externa.puerto + p.externa.ruta);
        var ext = !p.externa ? "" :
          (p.corriendo ? " Otra app (puerto " + p.externa.puerto + ")." :
                         " Otra app (puerto " + p.externa.puerto + "): ahora está apagada.");
        html += '<a class="zeia-nav-item" href="' + esc(p.url) + '"' +
                (actual ? ' aria-current="page"' : "") + ">" +
                '<span class="zeia-nav-t">' + esc(p.titulo) + "</span>" +
                '<span class="zeia-nav-r">' + esc(ruta) + "</span>" +
                (actual ? '<span class="zeia-nav-aqui">estás aquí</span>' : "") +
                "<small>" + esc(p.resumen + ext) + "</small>" +
                (av ? '<span class="zeia-nav-aviso">' + esc(av) + "</span>" : "") +
                "</a>";
        if (p.variantes && p.variantes.length > 1) {
          html += '<div class="zeia-nav-var">' + p.variantes.map(function (v) {
            return '<a href="' + esc(v.url) + '"' +
                   (esActual(v.url, true) ? ' aria-current="page"' : "") + ">" +
                   esc(v.etiqueta) + "</a>";
          }).join("") + "</div>";
        }
      });
    });
    lista.innerHTML = html;
    pie.innerHTML = "Bases: " + d.bases.map(function (b) {
      return (ICONO_BASE[b.id] || "") + " " + esc(b.label) + " " + esc(MODO[b.modo] || b.modo);
    }).join(" · ");
  }

  function abrir() {
    if (typeof dialogo.showModal !== "function") {  // navegador muy viejo
      location.href = "/";
      return;
    }
    if (!dialogo.open) dialogo.showModal();
    botones.forEach(function (b) { b.setAttribute("aria-expanded", "true"); });
    if (datos) render(datos);
    else lista.innerHTML = '<p class="zeia-nav-msg">Cargando…</p>';
    fetch("/api/indice", {cache: "no-store"})
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) { datos = d; render(d); })
      .catch(function (e) {
        if (!datos) {
          lista.innerHTML = '<p class="zeia-nav-msg">No pude cargar el listado (' +
                            esc(e.message) + '). El enlace de Inicio sí funciona.</p>';
        }
      });
  }

  function init() {
    var slots = document.querySelectorAll("[data-zeia-nav]");
    if (!slots.length || document.getElementById("zeia-nav-panel")) return;

    var st = document.createElement("style");
    st.textContent = CSS;
    document.head.appendChild(st);

    dialogo = document.createElement("dialog");
    dialogo.className = "zeia-nav";
    dialogo.id = "zeia-nav-panel";
    dialogo.setAttribute("aria-labelledby", "zeia-nav-titulo");
    dialogo.innerHTML =
      '<div class="zeia-nav-in">' +
        '<div class="zeia-nav-cab"><b id="zeia-nav-titulo">☰ Índice ZEIA</b>' +
          '<button type="button" class="zeia-nav-x">Cerrar ✕</button></div>' +
        '<a class="zeia-nav-inicio" href="/" autofocus><b>🏠 Inicio</b>' +
          "<small>Qué hace cada página, de dónde salen los datos y comandos</small></a>" +
        '<nav aria-label="Páginas"><div class="zeia-nav-lista"></div></nav>' +
        '<div class="zeia-nav-pie"></div>' +
      "</div>";
    document.body.appendChild(dialogo);
    lista = dialogo.querySelector(".zeia-nav-lista");
    pie = dialogo.querySelector(".zeia-nav-pie");

    dialogo.querySelector(".zeia-nav-x").addEventListener("click", function () {
      dialogo.close();
    });
    // Clic en el fondo oscuro (fuera del panel) = cerrar.
    dialogo.addEventListener("click", function (e) {
      if (e.target === dialogo) dialogo.close();
    });
    dialogo.addEventListener("close", function () {
      botones.forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
    });

    Array.prototype.forEach.call(slots, function (slot) {
      var b = document.createElement("button");
      b.type = "button";
      b.className = ("zeia-nav-btn " + (slot.getAttribute("data-clase") || "")).trim();
      b.innerHTML = '<span aria-hidden="true">☰</span> Índice';
      b.title = "Ver todas las páginas";
      b.setAttribute("aria-haspopup", "dialog");
      b.setAttribute("aria-expanded", "false");
      b.setAttribute("aria-controls", "zeia-nav-panel");
      b.addEventListener("click", abrir);
      slot.appendChild(b);
      botones.push(b);
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
