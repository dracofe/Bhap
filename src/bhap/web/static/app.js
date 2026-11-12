"use strict";
/* Bhap desktop UI — vanilla JS, no network beyond the local Bhap server. */

const $ = (s) => document.querySelector(s);
const S = {
  app: null, mode: "strict", lang: "id", view: "home",
  conversations: [], convId: null, messages: [], docs: [],
  busy: false, abort: null, scope: [], slashIndex: 0,
};

/* ------------------------------------------------------------------ i18n */
const L = {
  new: ["Chat baru", "New chat"], chats: ["Riwayat chat", "Chats"], docs: ["Dokumen", "Documents"],
  upload: ["Unggah materi", "Upload material"], recents: ["Terbaru", "Recents"], manage: ["Kelola dokumen", "Manage documents"],
  strip: ["Unggah materi, kode, atau arsip — semuanya diproses di laptop ini", "Upload documents, code or archives — processed on this laptop"],
  "docs-hint": ["Materi diindeks di laptop ini. Seret file ke jendela ini untuk mengunggah.",
                "Material is indexed on this laptop. Drag files onto this window to upload."],
  extracted: ["Teks hasil ekstraksi dari dokumen (bukan tampilan visual aslinya).", "Text extracted from the document (not its visual layout)."],
  "open-original": ["Buka file asli", "Open original file"], drop: ["Lepaskan file untuk mengunggah", "Drop files to upload"],
  placeholder: ["Tanya apa saja tentang materimu… ketik / untuk perintah", "Ask anything about your material… type / for commands"],
  placeholderEmpty: ["Unggah materi dulu dengan + atau /upload, lalu bertanya…", "Upload material with + or /upload first, then ask…"],
  searchChats: ["Cari percakapan…", "Search conversations…"],
};
const T = (k) => (L[k] ? L[k][S.lang === "en" ? 1 : 0] : k);
const tr = (id, en) => (S.lang === "en" ? en : id);
function modeInfo(m) {
  return {
    strict: [tr("Hanya sumber", "Source only"), tr("Jawab hanya dari materi, dengan sitasi. Menolak jika tidak ada.", "Answers only from your material, with citations.")],
    general: [tr("Sumber + umum", "Source + general"), tr("Materi dulu, boleh pengetahuan umum (diberi label). Cocok untuk tugas & kode.", "Material first, labelled general knowledge allowed. Good for tasks & code.")],
    brainstorm: ["Brainstorm", tr("Ide kreatif berdasarkan materi; inferensi diberi label.", "Creative ideas grounded in your material; inferences are labelled.")],
  }[m];
}

/* ------------------------------------------------------------------ api */
async function api(path, opts = {}) {
  const res = await fetch(path, { credentials: "same-origin", ...opts,
    headers: { "Content-Type": "application/json", ...(opts.headers || {}) } });
  let data = null;
  try { data = await res.json(); } catch (_) { /* empty */ }
  if (!res.ok) throw new Error((data && (data.error + (data.hint ? " — " + data.hint : ""))) || res.statusText);
  return data;
}
async function stream(path, body, onEvent, signal, headers) {
  const res = await fetch(path, { method: "POST", credentials: "same-origin", body, signal,
    headers: headers || { "Content-Type": "application/json" } });
  if (!res.ok) {
    let msg = res.statusText;
    try { const d = await res.json(); msg = d.error + (d.hint ? " — " + d.hint : ""); } catch (_) { /* keep */ }
    throw new Error(msg);
  }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, i).trim(); buf = buf.slice(i + 1);
      if (line) onEvent(JSON.parse(line));
    }
  }
}

/* ------------------------------------------------------------------ markdown (escape first → XSS-safe) */
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
function inline(text, cites) {
  return text
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])\*(?!\s)([^*\n]+?)\*(?=[\s).,;:!?]|$)/g, "$1<em>$2</em>")
    .replace(/(^|[^\w)])\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\]/g, (m, pre, list) => {
      const nums = list.split(/\s*,\s*/).map(Number);
      if (!cites) return pre + `<span class="cite">${nums.join(",")}</span>`;
      return pre + nums.map((n) => (cites.has(n) ? `<button class="cite" data-n="${n}">${n}</button>` : "")).join("");
    });
}
function md(src, cites) {
  const blocks = [];
  let text = esc(src).replace(/```([\w+-]*)[^\n]*\n([\s\S]*?)(?:```|$)/g, (m, lang, code) => {
    blocks.push(`<pre><div class="code-head"><span>${lang || "code"}</span><button class="copy">${tr("Salin", "Copy")}</button></div><code>${code.replace(/\n$/, "")}</code></pre>`);
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  const codes = [];
  text = text.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(`<code>${c}</code>`); return `\u0000C${codes.length - 1}\u0000`; });
  const lines = text.split("\n");
  const out = [];
  let para = [];
  const flush = () => { if (para.length) { out.push(`<p>${para.map((l) => inline(l, cites)).join("<br>")}</p>`); para = []; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\u0000B\d+\u0000$/.test(line.trim())) { flush(); out.push(line.trim()); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) { flush(); out.push(`<h${m[1].length}>${inline(m[2], cites)}</h${m[1].length}>`); continue; }
    if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      flush();
      const cells = (l) => l.trim().replace(/^\||\|$/g, "").split("|").map((c) => inline(c.trim(), cites));
      let html = "<table><thead><tr>" + cells(line).map((c) => `<th>${c}</th>`).join("") + "</tr></thead><tbody>";
      i += 2;
      while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { html += "<tr>" + cells(lines[i]).map((c) => `<td>${c}</td>`).join("") + "</tr>"; i++; }
      i--; out.push(html + "</tbody></table>"); continue;
    }
    if ((m = line.match(/^\s*([-*•]|\d+[.)])\s+(.*)$/))) {
      flush();
      const ordered = /\d/.test(m[1]);
      const items = [];
      while (i < lines.length && (m = lines[i].match(/^\s*([-*•]|\d+[.)])\s+(.*)$/))) { items.push(`<li>${inline(m[2], cites)}</li>`); i++; }
      i--; out.push(`<${ordered ? "ol" : "ul"}>${items.join("")}</${ordered ? "ol" : "ul"}>`); continue;
    }
    if ((m = line.match(/^&gt;\s?(.*)$/))) { flush(); out.push(`<blockquote>${inline(m[1], cites)}</blockquote>`); continue; }
    if (!line.trim()) { flush(); continue; }
    para.push(line);
  }
  flush();
  return out.join("")
    .replace(/\u0000B(\d+)\u0000/g, (m, n) => blocks[+n])
    .replace(/\u0000C(\d+)\u0000/g, (m, n) => codes[+n]);
}

/* ------------------------------------------------------------------ helpers */
function el(tag, cls, text) { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
function toast(text, kind = "", ms = 4200) {
  const t = el("div", "toast " + kind); t.append(el("div", "", text)); $("#toasts").append(t);
  if (ms) setTimeout(() => t.remove(), ms);
  return t;
}
function greeting() {
  const h = new Date().getHours();
  if (S.lang === "en") return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
  return h < 11 ? "Selamat pagi" : h < 15 ? "Selamat siang" : h < 18 ? "Selamat sore" : "Selamat malam";
}
function unitLabel(unit) { return { page: tr("halaman", "pages"), "page~": tr("halaman", "pages"), slide: "slide", line: tr("baris", "lines") }[unit] || unit; }
function size(n) { return n > 1048576 ? (n / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(n / 1024)) + " KB"; }
function when(ts) {
  const d = new Date(ts.replace(" ", "T") + "Z"); const diff = (Date.now() - d) / 1000;
  if (diff < 60) return tr("baru saja", "just now");
  if (diff < 3600) return Math.round(diff / 60) + tr(" mnt lalu", " min ago");
  if (diff < 86400) return Math.round(diff / 3600) + tr(" jam lalu", " h ago");
  return d.toLocaleDateString(S.lang === "en" ? "en" : "id", { day: "numeric", month: "short" });
}

/* ------------------------------------------------------------------ state & chrome */
async function refreshState() {
  S.app = await api("/api/state");
  S.mode = S.mode || S.app.mode; S.lang = S.app.language || "id";
  document.documentElement.lang = S.lang;
  document.querySelectorAll("[data-i18n]").forEach((n) => { n.textContent = T(n.dataset.i18n); });
  $("#chat-search").placeholder = T("searchChats");
  $("#ws-name").textContent = S.app.workspace;
  $("#ws-avatar").textContent = (S.app.workspace || "?")[0];
  const status = S.app.ready ? tr("Offline siap", "Offline ready") : !S.app.runtime_ok ? tr("Model tidak tersedia", "Model unavailable") : tr("Setup belum lengkap", "Setup incomplete");
  $("#ws-sub").textContent = `${status} · ${S.app.llm_model || "-"}`;
  const dot = $("#status-pill .dot");
  dot.className = "dot " + (S.app.ready ? "ok" : S.app.runtime_ok ? "warn" : "bad");
  $("#status-text").textContent = `${status} · ${S.app.stats.indexed} ${tr("dokumen", "documents")}`;
  $("#greeting").textContent = greeting();
  $("#input").placeholder = S.app.stats.indexed ? T("placeholder") : T("placeholderEmpty");
  const notice = $("#home-notice");
  if (!S.app.ready) {
    notice.classList.remove("hidden");
    notice.innerHTML = !S.app.runtime_ok
      ? tr("Runtime model lokal belum berjalan. Pastikan Ollama terpasang, lalu jalankan <code>bhap setup</code> di terminal.",
           "The local model runtime is not running. Make sure Ollama is installed, then run <code>bhap setup</code>.")
      : tr("Model belum dipilih/terpasang. Jalankan <code>bhap setup</code> di terminal.", "Models are not set up. Run <code>bhap setup</code> in a terminal.");
  } else if (S.app.stale) {
    notice.classList.remove("hidden");
    notice.innerHTML = esc(`${S.app.stale} ` + tr("dokumen perlu diindeks ulang:", "documents need reindexing:")) + " <code>bhap docs reindex --stale</code>";
  } else notice.classList.add("hidden");
  setMode(S.mode, false);
}
function setMode(m, save = true) {
  S.mode = m;
  $("#mode-label").textContent = modeInfo(m)[0];
  if (save) api("/api/settings", { method: "POST", body: JSON.stringify({ mode: m }) }).catch(() => {});
}
function show(view) {
  S.view = view;
  for (const v of ["home", "chat", "chats", "docs"]) $("#view-" + v).classList.toggle("hidden", v !== view);
  const composer = $("#composer");
  if (view === "home") $("#home-composer-slot").append(composer);
  if (view === "chat") $("#dock-composer-slot").append(composer);
  composer.classList.toggle("hidden", !(view === "home" || view === "chat"));
  $("#seg-chat").classList.toggle("active", view !== "docs");
  $("#seg-docs").classList.toggle("active", view === "docs");
  const conv = S.conversations.find((c) => c.id === S.convId);
  $("#topbar-title").textContent = view === "chat" && conv ? conv.title : "";
  if (view === "docs") loadDocs();
  if (view === "chats") renderChatList();
  if (view === "home" || view === "chat") setTimeout(() => $("#input").focus(), 0);
}

/* ------------------------------------------------------------------ conversations */
async function loadConversations() {
  S.conversations = await api("/api/conversations");
  const box = $("#recents"); box.innerHTML = "";
  if (!S.conversations.length) box.append(el("div", "recents-empty", tr("Belum ada chat.", "No chats yet.")));
  for (const c of S.conversations.slice(0, 30)) {
    const row = el("div", "recent" + (c.id === S.convId ? " active" : ""));
    const t = el("button", "t", c.title); t.title = c.title; t.onclick = () => openConversation(c.id);
    const x = el("button", "x", "×"); x.title = tr("Hapus", "Delete");
    x.onclick = async (e) => { e.stopPropagation(); await deleteConversation(c.id); };
    row.append(t, x); box.append(row);
  }
  if (S.view === "chats") renderChatList();
}
function renderChatList() {
  const q = $("#chat-search").value.toLowerCase();
  const list = $("#chat-list"); list.innerHTML = "";
  const rows = S.conversations.filter((c) => c.title.toLowerCase().includes(q));
  if (!rows.length) { const e = el("div", "empty"); e.append(el("p", "", tr("Belum ada percakapan.", "No conversations yet."))); list.append(e); return; }
  for (const c of rows) {
    const r = el("div", "row");
    const m = el("button", "main-col"); m.append(el("div", "title", c.title), el("div", "sub", `${c.messages} ${tr("pesan", "messages")} · ${when(c.updated_at)}`));
    m.onclick = () => openConversation(c.id);
    const d = el("button", "btn danger", tr("Hapus", "Delete")); d.onclick = () => deleteConversation(c.id);
    r.append(m, d); list.append(r);
  }
}
async function deleteConversation(id) {
  if (!confirm(tr("Hapus percakapan ini?", "Delete this conversation?"))) return;
  await api("/api/conversations/" + id, { method: "DELETE" });
  if (S.convId === id) newChat();
  loadConversations();
}
async function openConversation(id) {
  const data = await api("/api/conversations/" + id);
  S.convId = id; S.messages = [];
  const thread = $("#thread"); thread.innerHTML = "";
  for (const m of data.messages) {
    if (m.role === "user") addUser(m.text);
    else {
      const cites = (m.citations || []).map((c) => ({ marker: c.marker, chunk_id: c.chunk_id,
        file: c.locator.split(" — ")[0], locator: c.locator.split(" — ").slice(1).join(" — "), excerpt: c.evidence_excerpt }));
      const view = addBot();
      finishAnswer(view, { answer: m.text, citations: cites, status: m.status || "answered", mode: m.mode });
    }
  }
  show("chat"); loadConversations();
  thread.scrollTop = thread.scrollHeight;
}
function newChat() {
  S.convId = null; S.messages = []; $("#thread").innerHTML = ""; S.scope = []; renderScope();
  show("home"); loadConversations();
}

/* ------------------------------------------------------------------ chat rendering */
function addUser(text) {
  const m = el("div", "msg user"); m.append(el("div", "bubble", text)); $("#thread").append(m); return m;
}
function addBot() {
  const m = el("div", "msg bot");
  const who = el("img", "who"); who.src = "bhap.svg"; who.alt = "";
  const a = el("div", "answer"); const body = el("div", "md");
  a.append(body); m.append(who, a); $("#thread").append(m);
  return { root: m, answer: a, body };
}
function bindAnswer(view, result) {
  const byMarker = new Map((result.citations || []).map((c) => [c.marker, c]));
  view.body.querySelectorAll(".cite[data-n]").forEach((b) => { b.onclick = () => openSource(byMarker.get(+b.dataset.n)); });
  view.body.querySelectorAll("pre .copy").forEach((b) => {
    b.onclick = () => { navigator.clipboard.writeText(b.closest("pre").querySelector("code").textContent); b.textContent = tr("Tersalin", "Copied"); };
  });
}
function finishAnswer(view, result) {
  const cites = new Set((result.citations || []).map((c) => c.marker));
  view.body.classList.remove("caret");
  view.body.innerHTML = md(result.answer || "", cites);
  view.answer.classList.toggle("dim", result.status === "abstained" || result.status === "unsupported");
  bindAnswer(view, result);
  if (result.citations && result.citations.length) {
    const d = el("details", "sources"); d.open = result.citations.length <= 3;
    d.append(el("summary", "", `${tr("Sumber", "Sources")} (${result.citations.length})`));
    for (const c of result.citations) {
      const b = el("button", "src");
      const n = el("span", "cite n", String(c.marker));
      const text = el("span", "src-text");
      const excerpt = (c.excerpt || "").replace(/^#+\s*/, "").replace(/\s#+\s/g, " ");
      text.append(el("span", "where", `${c.file} — ${c.locator}`), el("span", "ex", excerpt ? `“${excerpt}”` : ""));
      b.append(n, text);
      b.onclick = () => openSource(c); d.append(b);
    }
    view.answer.append(d);
  }
  if (result.status === "unsupported" && result.mode === "strict") {
    const h = el("div", "hint", tr("Model punya jawaban, tapi tidak bersumber dari materi Anda. Untuk tugas umum (mis. menulis/melengkapi kode), izinkan pengetahuan umum.",
                                   "The model has an answer, but not from your material. For general tasks (e.g. code), allow general knowledge."));
    const btn = el("button", "btn primary", tr("Jawab dengan Sumber + umum", "Answer with Source + general"));
    btn.onclick = () => { setMode("general"); send(result.question); };
    h.append(el("br"), btn); view.answer.append(h);
  } else if (result.status === "abstained" && result.missing_terms && result.missing_terms.length) {
    view.answer.append(el("div", "hint", tr("Tidak ada di koleksi: ", "Not in your collection: ") + result.missing_terms.slice(0, 8).join(", ")
      + tr(". Unggah materi tentang topik ini atau coba kata kunci lain.", ". Upload material on this topic or try other keywords.")));
  }
  if (result.model) {
    const meta = el("div", "meta");
    meta.append(el("span", "", `${result.model} · ${modeInfo(result.mode || S.mode)[0]}${result.seconds ? " · " + result.seconds + "s" : ""}`));
    const copy = el("button", "", tr("Salin", "Copy")); copy.onclick = () => { navigator.clipboard.writeText(result.answer); copy.textContent = tr("Tersalin", "Copied"); };
    meta.append(copy); view.answer.append(meta);
  }
}

async function send(text) {
  const question = (text ?? $("#input").value).trim();
  if (!question || S.busy) return;
  if (question.startsWith("/") && !text) { runSlash(question); return; }
  $("#input").value = ""; autosize(); closeSlash();
  if (S.view !== "chat") show("chat");
  addUser(question);
  const view = addBot();
  view.body.innerHTML = `<span class="thinking"><i></i><i></i><i></i> ${tr("Mencari di materi lokal…", "Searching your material…")}</span>`;
  const thread = $("#thread"); thread.scrollTop = thread.scrollHeight;
  S.busy = true; $("#composer").classList.add("busy");
  const ctrl = new AbortController(); S.abort = ctrl;
  let acc = ""; let pending = false; let result = null; let finished = false;
  const paint = () => { pending = false; if (finished) return; view.body.innerHTML = md(acc, null); view.body.classList.add("caret");
    const near = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 160; if (near) thread.scrollTop = thread.scrollHeight; };
  try {
    await stream("/api/ask", JSON.stringify({ question, conversation_id: S.convId, mode: S.mode, doc_ids: S.scope.map((d) => d.id) }), (ev) => {
      if (ev.type === "token") { acc += ev.text; if (!pending) { pending = true; requestAnimationFrame(paint); } }
      else if (ev.type === "done") result = ev.result;
      else if (ev.type === "error") throw new Error(ev.error + (ev.hint ? " — " + ev.hint : ""));
    }, ctrl.signal);
    finished = true;
    if (result) {
      finishAnswer(view, result);
      if (result.conversation_id && result.conversation_id !== S.convId) { S.convId = result.conversation_id; }
      loadConversations().then(() => show("chat"));
    }
  } catch (err) {
    finished = true;
    if (err.name === "AbortError") {
      view.body.classList.remove("caret");
      view.body.innerHTML = md(acc || "", null);
      view.answer.append(el("div", "meta", tr("Dihentikan.", "Stopped.")));
    } else {
      view.body.classList.remove("caret");
      view.body.innerHTML = ""; view.answer.append(el("div", "hint", "⚠ " + err.message));
    }
  } finally {
    S.busy = false; S.abort = null; $("#composer").classList.remove("busy");
    thread.scrollTop = thread.scrollHeight; refreshState().catch(() => {});
  }
}

/* ------------------------------------------------------------------ sources drawer */
let drawerDoc = null;
async function openSource(c) {
  if (!c) return;
  const data = await api("/api/chunk/" + c.chunk_id).catch((e) => { toast(e.message, "err"); return null; });
  if (!data) return;
  drawerDoc = data;
  $("#drawer-file").textContent = data.file;
  $("#drawer-loc").textContent = data.locator;
  const warn = $("#drawer-warn");
  const notes = [];
  if (data.visual) notes.push(tr("⚠ Konten visual di lokasi ini mungkin tidak terbaca.", "⚠ Visual content here may not have been interpreted."));
  if (data.kind === "ocr") notes.push(`⚠ OCR ${Math.round(data.ocr_conf || 0)}% — ` + tr("verifikasi dengan file asli.", "verify against the original."));
  warn.textContent = notes.join(" "); warn.classList.toggle("hidden", !notes.length);
  const body = $("#drawer-body"); body.innerHTML = "";
  for (const part of data.context) {
    const span = el("span", part.current ? "cur" : "ctx", part.text + (part.current ? "" : "\n"));
    body.append(span);
    if (part.current) setTimeout(() => span.scrollIntoView({ block: "center" }), 30);
  }
  $("#drawer").classList.remove("hidden");
}
$("#drawer-close").onclick = () => $("#drawer").classList.add("hidden");
$("#drawer-open").onclick = async () => {
  if (!drawerDoc) return;
  try { await api(`/api/open/${drawerDoc.document_id}?page=${drawerDoc.page || ""}`, { method: "POST" }); }
  catch (e) { toast(e.message, "err"); }
};

/* ------------------------------------------------------------------ documents & upload */
async function loadDocs() {
  S.docs = await api("/api/documents");
  const list = $("#doc-list"); list.innerHTML = "";
  if (!S.docs.length) {
    const e = el("div", "empty"); const img = el("img"); img.src = "bhap.svg"; img.alt = "";
    e.append(img, el("p", "", tr("Belum ada dokumen. Unggah PDF, Office, kode (Java, C, Python…), ZIP, gambar, dan lainnya.",
                                 "No documents yet. Upload PDF, Office, source code (Java, C, Python…), ZIP, images and more.")));
    const b = el("button", "btn primary", "+ " + T("upload")); b.onclick = pickFiles; e.append(b); list.append(e); return;
  }
  for (const d of S.docs) {
    const r = el("div", "row");
    r.append(el("div", "kind " + d.kind, (d.kind || "?").toUpperCase()));
    const m = el("div", "main-col");
    m.append(el("div", "title", d.file),
             el("div", "sub", `${d.units} ${unitLabel(d.unit)} · ${d.chunks} chunk · ${size(d.size)} · ${when(d.updated_at)}`));
    if (d.warnings.length) { const w = el("div", "warn", "⚠ " + d.warnings[0] + (d.warnings.length > 1 ? ` (+${d.warnings.length - 1})` : "")); w.title = d.warnings.join("\n"); m.append(w); }
    const ask = el("button", "btn", tr("Tanya file ini", "Ask this file"));
    ask.onclick = () => { S.scope = [{ id: d.id, file: d.file }]; renderScope(); newChatKeepScope(); };
    const del = el("button", "btn danger", tr("Hapus", "Delete"));
    del.onclick = async () => {
      if (!confirm(tr(`Hapus ${d.file} beserta indeksnya?`, `Delete ${d.file} and its index?`))) return;
      await api("/api/documents/" + d.id, { method: "DELETE" }); toast(tr("Dokumen dihapus.", "Document deleted."), "ok"); loadDocs(); refreshState();
    };
    r.append(m, ask, del); list.append(r);
  }
}
function newChatKeepScope() { S.convId = null; $("#thread").innerHTML = ""; show("home"); }
function renderScope() {
  const box = $("#scope"); box.innerHTML = "";
  box.classList.toggle("hidden", !S.scope.length);
  for (const d of S.scope) {
    const c = el("span", "chip", "📄 " + d.file); const x = el("button", "", "×");
    x.onclick = () => { S.scope = S.scope.filter((s) => s.id !== d.id); renderScope(); }; c.append(x); box.append(c);
  }
}
function pickFiles() { $("#file").value = ""; $("#file").click(); }
async function uploadFiles(files) {
  for (const f of files) {
    const t = toast("", "", 0); t.innerHTML = "";
    const title = el("div", "", "⬆ " + f.name); const small = el("div", "small", tr("Mengunggah…", "Uploading…"));
    const bar = el("div", "bar"); const fill = el("i"); bar.append(fill); t.append(title, small, bar);
    const stages = { extracting: [tr("Mengekstrak teks…", "Extracting text…"), 15], chunking: [tr("Memecah per bagian…", "Chunking…"), 30],
                     embedding: [tr("Membuat embedding…", "Embedding…"), 40], indexed: [tr("Terindeks", "Indexed"), 100] };
    try {
      let result = null;
      await stream("/api/upload?name=" + encodeURIComponent(f.name), f, (ev) => {
        if (ev.type === "stage") {
          const [label, pct] = stages[ev.stage] || [ev.stage, 10];
          let p = pct; let lbl = label;
          if (ev.stage === "embedding" && ev.total) { p = 40 + 55 * (ev.done / ev.total); lbl += ` ${ev.done}/${ev.total}`; }
          small.textContent = lbl; fill.style.width = p + "%";
        } else if (ev.type === "done") result = ev.result;
        else if (ev.type === "error") throw new Error(ev.error);
      }, null, { "Content-Type": "application/octet-stream" });
      t.classList.add("ok"); fill.style.width = "100%";
      if (result.status === "duplicate") small.textContent = tr(`Sudah ada sebagai ${result.other}`, `Already indexed as ${result.other}`);
      else if (result.status === "unchanged") small.textContent = tr("Sudah terindeks", "Already indexed");
      else {
        small.textContent = `${result.units} ${unitLabel(result.unit)} · ${result.chunks} chunk · ${tr("terindeks lokal", "indexed locally")} · ${result.seconds}s`;
        if (result.warnings && result.warnings.length) t.append(el("div", "small", "⚠ " + result.warnings[0]));
      }
      setTimeout(() => t.remove(), 6000);
    } catch (e) {
      t.classList.add("err"); small.textContent = e.message; setTimeout(() => t.remove(), 9000);
    }
  }
  await refreshState();
  if (S.view === "docs") loadDocs();
}
$("#file").onchange = (e) => uploadFiles([...e.target.files]);
let dragDepth = 0;
window.addEventListener("dragenter", (e) => { if ([...e.dataTransfer.types].includes("Files")) { dragDepth++; $("#dropzone").classList.remove("hidden"); } });
window.addEventListener("dragleave", () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) $("#dropzone").classList.add("hidden"); });
window.addEventListener("dragover", (e) => e.preventDefault());
window.addEventListener("drop", (e) => { e.preventDefault(); dragDepth = 0; $("#dropzone").classList.add("hidden"); if (e.dataTransfer.files.length) uploadFiles([...e.dataTransfer.files]); });

/* ------------------------------------------------------------------ slash commands */
function slashCommands() {
  return [
    ["/upload", tr("Unggah file materi", "Upload material"), () => pickFiles()],
    ["/mode strict", modeInfo("strict")[0], () => setMode("strict")],
    ["/mode general", modeInfo("general")[0] + tr(" — untuk tugas & kode", " — for tasks & code"), () => setMode("general")],
    ["/mode brainstorm", modeInfo("brainstorm")[0], () => setMode("brainstorm")],
    ["/new", T("new"), () => newChat()],
    ["/docs", T("docs"), () => show("docs")],
    ["/chats", T("chats"), () => show("chats")],
  ];
}
function renderSlash() {
  const v = $("#input").value;
  const box = $("#slash");
  if (!v.startsWith("/") || v.includes("\n")) return closeSlash();
  const items = slashCommands().filter(([cmd]) => cmd.startsWith(v.trim()) || v.trim() === "/");
  if (!items.length) return closeSlash();
  S.slashIndex = Math.min(S.slashIndex, items.length - 1);
  box.innerHTML = "";
  items.forEach(([cmd, desc], i) => {
    const b = el("button", i === S.slashIndex ? "sel" : ""); b.append(el("code", "", cmd), el("span", "", desc));
    b.onmousedown = (e) => { e.preventDefault(); runSlash(cmd); }; box.append(b);
  });
  box.classList.remove("hidden");
}
function closeSlash() { $("#slash").classList.add("hidden"); S.slashIndex = 0; }
function runSlash(text) {
  const cmd = slashCommands().find(([c]) => c === text.trim()) || slashCommands().find(([c]) => c.startsWith(text.trim()));
  $("#input").value = ""; autosize(); closeSlash();
  if (cmd) cmd[2](); else toast(tr("Perintah tidak dikenal: ", "Unknown command: ") + text, "err");
}

/* ------------------------------------------------------------------ composer */
function autosize() { const t = $("#input"); t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, window.innerHeight * 0.4) + "px"; }
$("#input").addEventListener("input", () => { autosize(); renderSlash(); });
$("#input").addEventListener("keydown", (e) => {
  const open = !$("#slash").classList.contains("hidden");
  if (open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault(); const n = $("#slash").children.length;
    S.slashIndex = (S.slashIndex + (e.key === "ArrowDown" ? 1 : n - 1)) % n; renderSlash(); return;
  }
  if (open && (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey))) {
    e.preventDefault(); const sel = $("#slash").children[S.slashIndex]; if (sel) runSlash(sel.querySelector("code").textContent); return;
  }
  if (e.key === "Escape") closeSlash();
  if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
});
$("#send").onclick = () => { if (S.busy && S.abort) S.abort.abort(); else send(); };
$("#attach").onclick = pickFiles;
$("#mode-btn").onclick = (e) => {
  e.stopPropagation();
  const menu = $("#mode-menu"); menu.innerHTML = "";
  for (const m of ["strict", "general", "brainstorm"]) {
    const [name, desc] = modeInfo(m);
    const b = el("button"); const wrap = el("span"); wrap.append(el("span", "", name), el("span", "desc", desc));
    b.append(wrap); if (m === S.mode) b.append(el("span", "check", "✓"));
    b.onclick = () => { setMode(m); menu.classList.add("hidden"); }; menu.append(b);
  }
  menu.classList.toggle("hidden");
};

/* ------------------------------------------------------------------ workspace menu */
$("#account").onclick = (e) => {
  e.stopPropagation();
  const menu = $("#account-menu"); menu.innerHTML = "";
  menu.append(el("div", "label", "Workspace"));
  for (const w of S.app.workspaces) {
    const b = el("button"); b.append(el("span", "", `${w.name}`), el("span", "desc", ` ${w.documents} ${tr("dok", "docs")}`));
    if (w.name === S.app.workspace) b.append(el("span", "check", "✓"));
    b.onclick = () => switchWorkspace(w.name, false); menu.append(b);
  }
  const add = el("button", "", "+ " + tr("Workspace baru…", "New workspace…"));
  add.onclick = () => { const n = prompt(tr("Nama workspace baru:", "New workspace name:")); if (n) switchWorkspace(n.trim(), true); };
  menu.append(add, el("hr"));
  const lang = el("button", "", S.lang === "en" ? "Bahasa Indonesia" : "English");
  lang.onclick = async () => { await api("/api/settings", { method: "POST", body: JSON.stringify({ language: S.lang === "en" ? "id" : "en" }) }); location.reload(); };
  menu.append(lang);
  menu.classList.toggle("hidden");
};
async function switchWorkspace(name, create) {
  try { await api("/api/workspace", { method: "POST", body: JSON.stringify({ name, create }) }); }
  catch (e) { toast(e.message, "err"); return; }
  $("#account-menu").classList.add("hidden");
  await refreshState(); newChat(); toast(tr("Workspace aktif: ", "Active workspace: ") + name, "ok");
}
document.addEventListener("click", (e) => {
  if (!e.target.closest("#account-menu, #account")) $("#account-menu").classList.add("hidden");
  if (!e.target.closest("#mode-menu, #mode-btn")) $("#mode-menu").classList.add("hidden");
});

/* ------------------------------------------------------------------ chrome wiring */
$("#nav-new").onclick = newChat;
$("#nav-chats").onclick = () => show("chats");
$("#nav-docs").onclick = () => show("docs");
$("#nav-upload").onclick = pickFiles;
$("#docs-upload").onclick = pickFiles;
$("#upload-strip").onclick = pickFiles;
$("#status-pill").onclick = () => show("docs");
$("#seg-chat").onclick = () => show(S.convId ? "chat" : "home");
$("#seg-docs").onclick = () => show("docs");
$("#chat-search").oninput = renderChatList;
$("#collapse").onclick = () => { $("#app").classList.add("collapsed"); $("#expand").classList.remove("hidden"); };
$("#expand").onclick = () => { $("#app").classList.remove("collapsed"); $("#expand").classList.add("hidden"); };
function applyTheme(t) { document.documentElement.dataset.theme = t; try { localStorage.setItem("bhap-theme", t); } catch (_) { /* ignore */ } }
$("#theme").onclick = () => applyTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light");
try { applyTheme(localStorage.getItem("bhap-theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark")); } catch (_) { applyTheme("dark"); }
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); newChat(); }
  if (e.key === "Escape") $("#drawer").classList.add("hidden");
});
setInterval(() => api("/api/heartbeat", { method: "POST" }).catch(() => {}), 15000);

(async function init() {
  try {
    await refreshState();
    S.mode = S.app.mode;
    setMode(S.mode, false);
    await loadConversations();
    show("home");
  } catch (e) {
    document.body.innerHTML = `<div style="padding:40px;font-family:system-ui">Bhap: ${esc(e.message)}</div>`;
  }
})();
