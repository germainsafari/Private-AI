(() => {
  "use strict";

  const API_BASE = (
    document.querySelector('meta[name="api-base"]')?.getAttribute("content") || ""
  ).replace(/\/$/, "");
  const apiUrl = (path) => `${API_BASE}${path}`;

  const STORAGE_KEY = "inference.chats.v2";
  const ACTIVE_KEY = "inference.active.v2";
  const MAX_IMAGE_SIZE = 4 * 1024 * 1024; // 4MB limit for base64 data URLs
  const MAX_FILE_SIZE = 25 * 1024 * 1024; // server ingest default
  let MAX_DIRECT_IMAGES = 4; // vLLM --limit-mm-per-prompt; refreshed from /api/ingest/formats
  let DOC_EXT = new Set([
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".json",
    ".xlsx",
    ".pptx",
  ]);

  const $ = (sel) => document.querySelector(sel);
  const els = {
    app: document.querySelector(".app"),
    sidebar: $("#sidebar"),
    chatList: $("#chatList"),
    newChatBtn: $("#newChatBtn"),
    collapseBtn: $("#collapseBtn"),
    openSidebarBtn: $("#openSidebarBtn"),
    clearBtn: $("#clearBtn"),
    modelName: $("#modelName"),
    modelPill: $("#modelPill"),
    visionBadge: $("#visionBadge"),
    chat: $("#chat"),
    messages: $("#messages"),
    welcome: $("#welcome"),
    composer: $("#composer"),
    input: $("#input"),
    sendBtn: $("#sendBtn"),
    stopBtn: $("#stopBtn"),
    attachBtn: $("#attachBtn"),
    fileInput: $("#fileInput"),
    imagePreviewContainer: $("#imagePreviewContainer"),
    imagePreviews: $("#imagePreviews"),
    dragOverlay: $("#dragOverlay"),
    outputBar: $("#outputBar"),
  };

  // ---------- State ----------
  let chats = [];
  let activeId = null;
  let abortController = null;
  let isStreaming = false;
  let pendingAttachments = []; // { kind: 'image'|'document', file, dataUrl?, name? }[]
  let outputMode = "auto";

  // ---------- Generated files (IndexedDB; localStorage is too small for them) ----------
  const fileStore = (() => {
    let dbPromise = null;
    const open = () =>
      (dbPromise ||= new Promise((resolve, reject) => {
        const req = indexedDB.open("inference.files", 1);
        req.onupgradeneeded = () => req.result.createObjectStore("files");
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
      }));
    const run = async (mode, fn) => {
      const db = await open();
      return new Promise((resolve, reject) => {
        const tx = db.transaction("files", mode);
        const req = fn(tx.objectStore("files"));
        tx.oncomplete = () => resolve(req?.result);
        tx.onerror = () => reject(tx.error);
      });
    };
    return {
      put: (key, value) => run("readwrite", (s) => s.put(value, key)),
      get: (key) => run("readonly", (s) => s.get(key)).catch(() => undefined),
      del: (key) => run("readwrite", (s) => s.delete(key)).catch(() => undefined),
    };
  })();

  const forgetArtifacts = (messages) => {
    (messages || []).forEach((m) =>
      (m.meta?.artifacts || []).forEach((a) => a.id && fileStore.del(a.id))
    );
  };

  // ---------- Utils ----------
  const uid = () =>
    Date.now().toString(36) + Math.random().toString(36).slice(2, 8);

  const save = () => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(chats));
      if (activeId) localStorage.setItem(ACTIVE_KEY, activeId);
    } catch (e) {
      console.warn("localStorage save failed", e);
    }
  };

  const load = () => {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) chats = JSON.parse(raw) || [];
      activeId = localStorage.getItem(ACTIVE_KEY) || null;
    } catch (e) {
      chats = [];
      activeId = null;
    }
  };

  const getChat = (id) => chats.find((c) => c.id === id) || null;
  const activeChat = () => getChat(activeId);

  const titleFromMessage = (text) => {
    const t = (text || "").trim().replace(/\s+/g, " ");
    if (!t) return "New chat";
    return t.length > 40 ? t.slice(0, 40) + "…" : t;
  };

  const escapeHtml = (s) =>
    s
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");

  const renderMarkdown = (text) => {
    if (typeof window.marked === "undefined") return escapeHtml(text);
    try {
      window.marked.setOptions({
        breaks: true,
        gfm: true,
        headerIds: false,
        mangle: false,
      });
      const raw = window.marked.parse(text || "");
      if (typeof window.DOMPurify !== "undefined") {
        return window.DOMPurify.sanitize(raw);
      }
      return raw;
    } catch (e) {
      return escapeHtml(text);
    }
  };

  const enhanceCodeBlocks = (container) => {
    container.querySelectorAll("pre").forEach((pre) => {
      if (pre.dataset.enhanced) return;
      pre.dataset.enhanced = "1";
      const codeEl = pre.querySelector("code");
      let lang = "";
      if (codeEl) {
        const cls = codeEl.className || "";
        const m = cls.match(/language-([\w-]+)/);
        if (m) lang = m[1];
      }
      const header = document.createElement("div");
      header.className = "code-header";
      const label = document.createElement("span");
      label.textContent = lang || "code";
      const btn = document.createElement("button");
      btn.className = "copy-btn";
      btn.textContent = "Copy";
      btn.type = "button";
      btn.addEventListener("click", async () => {
        try {
          await navigator.clipboard.writeText(codeEl ? codeEl.textContent : "");
          btn.textContent = "Copied";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        } catch {
          btn.textContent = "Failed";
          setTimeout(() => (btn.textContent = "Copy"), 1500);
        }
      });
      header.appendChild(label);
      header.appendChild(btn);
      pre.insertBefore(header, pre.firstChild);
    });
  };

  // ---------- Attachments (images + documents) ----------
  const extOf = (name) => {
    const i = name.lastIndexOf(".");
    return i >= 0 ? name.slice(i).toLowerCase() : "";
  };

  const isDocumentFile = (file) => DOC_EXT.has(extOf(file.name || ""));
  const isImageFile = (file) => (file.type || "").startsWith("image/");

  const fileToDataUrl = (file) => {
    return new Promise((resolve, reject) => {
      if (file.size > MAX_IMAGE_SIZE) {
        reject(new Error(`Image too large (max ${MAX_IMAGE_SIZE / 1024 / 1024}MB)`));
        return;
      }
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error);
      reader.readAsDataURL(file);
    });
  };

  const addPendingFile = async (file) => {
    if (!file) return;
    const name = file.name || `pasted-${Date.now()}.png`;
    // Large images go through ingest, which downscales them before the vision model.
    if (isImageFile(file) && file.size <= MAX_IMAGE_SIZE) {
      try {
        const dataUrl = await fileToDataUrl(file);
        pendingAttachments.push({ kind: "image", file, dataUrl, name });
        renderPendingAttachments();
      } catch (e) {
        alert(e.message || "Failed to load image");
      }
      return;
    }
    if (isImageFile(file) && file.size <= MAX_FILE_SIZE) {
      pendingAttachments.push({ kind: "document", file, name });
      renderPendingAttachments();
      return;
    }
    if (isDocumentFile(file)) {
      if (file.size > MAX_FILE_SIZE) {
        alert(`File too large (max ${MAX_FILE_SIZE / 1024 / 1024}MB): ${file.name}`);
        return;
      }
      pendingAttachments.push({ kind: "document", file, name: file.name });
      renderPendingAttachments();
      return;
    }
    alert(`Unsupported file type: ${file.name || "unknown"}`);
  };

  const removePendingAttachment = (index) => {
    pendingAttachments.splice(index, 1);
    renderPendingAttachments();
  };

  const clearPendingAttachments = () => {
    pendingAttachments = [];
    renderPendingAttachments();
  };

  const renderPendingAttachments = () => {
    els.imagePreviews.innerHTML = "";
    if (pendingAttachments.length === 0) {
      els.imagePreviewContainer.classList.add("hidden");
      return;
    }
    els.imagePreviewContainer.classList.remove("hidden");
    pendingAttachments.forEach((item, i) => {
      const removeBtn = document.createElement("button");
      removeBtn.className = "remove-btn";
      removeBtn.type = "button";
      removeBtn.innerHTML =
        '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18M6 6l12 12"/></svg>';
      removeBtn.addEventListener("click", () => removePendingAttachment(i));

      if (item.kind === "image") {
        const preview = document.createElement("div");
        preview.className = "image-preview";
        const imgEl = document.createElement("img");
        imgEl.src = item.dataUrl;
        imgEl.alt = "Preview";
        preview.appendChild(imgEl);
        preview.appendChild(removeBtn);
        els.imagePreviews.appendChild(preview);
      } else {
        const preview = document.createElement("div");
        preview.className = "file-preview";
        const icon = document.createElement("span");
        icon.className = "file-icon";
        icon.textContent = "📄";
        const name = document.createElement("span");
        name.className = "file-name";
        name.textContent = item.name || "Document";
        name.title = item.name || "";
        preview.appendChild(icon);
        preview.appendChild(name);
        preview.appendChild(removeBtn);
        els.imagePreviews.appendChild(preview);
      }
    });
  };

  const ingestDocuments = async (docItems) => {
    const fd = new FormData();
    docItems.forEach((d) => fd.append("files", d.file, d.name || d.file.name));
    const res = await fetch(apiUrl("/api/ingest"), { method: "POST", body: fd });
    if (!res.ok) {
      let detail = "Document parse failed";
      try {
        const err = await res.json();
        detail = err.detail || detail;
      } catch {
        /* ignore */
      }
      throw new Error(detail);
    }
    return res.json();
  };

  // Extract text content from multimodal message
  const getTextFromContent = (content) => {
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content
        .filter((c) => c.type === "text")
        .map((c) => c.text)
        .join("\n");
    }
    return "";
  };

  // Extract images from multimodal message
  const getImagesFromContent = (content) => {
    if (!Array.isArray(content)) return [];
    return content
      .filter((c) => c.type === "image_url")
      .map((c) => c.image_url?.url || "");
  };

  // ---------- Chat CRUD ----------
  const createChat = () => {
    const c = {
      id: uid(),
      title: "New chat",
      messages: [],
      createdAt: Date.now(),
      updatedAt: Date.now(),
    };
    chats.unshift(c);
    activeId = c.id;
    save();
    renderSidebar();
    renderMessages();
    return c;
  };

  const deleteChat = (id) => {
    forgetArtifacts(getChat(id)?.messages);
    chats = chats.filter((c) => c.id !== id);
    if (activeId === id) activeId = chats[0]?.id || null;
    if (!activeId) createChat();
    save();
    renderSidebar();
    renderMessages();
  };

  const selectChat = (id) => {
    if (activeId === id) return;
    activeId = id;
    clearPendingAttachments();
    save();
    renderSidebar();
    renderMessages();
  };

  const clearActive = () => {
    const c = activeChat();
    if (!c) return;
    forgetArtifacts(c.messages);
    c.messages = [];
    c.title = "New chat";
    c.updatedAt = Date.now();
    clearPendingAttachments();
    save();
    renderSidebar();
    renderMessages();
  };

  const displayUserText = (m) => {
    if (m.userText != null && m.userText !== "") return m.userText;
    const raw = getTextFromContent(m.content);
    const marker = "\n\n--- Attached documents ---";
    const idx = raw.indexOf(marker);
    if (idx >= 0) return raw.slice(0, idx).trim() || raw;
    return raw;
  };

  // ---------- Rendering ----------
  const renderSidebar = () => {
    els.chatList.innerHTML = "";
    chats.forEach((c) => {
      const item = document.createElement("div");
      item.className = "chat-item" + (c.id === activeId ? " active" : "");
      item.dataset.id = c.id;

      const title = document.createElement("div");
      title.className = "title";
      title.textContent = c.title || "New chat";

      const del = document.createElement("button");
      del.className = "del";
      del.type = "button";
      del.title = "Delete chat";
      del.innerHTML =
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2m3 0v14a2 2 0 01-2 2H7a2 2 0 01-2-2V6h14z"/></svg>';
      del.addEventListener("click", (e) => {
        e.stopPropagation();
        if (confirm("Delete this chat?")) deleteChat(c.id);
      });

      item.appendChild(title);
      item.appendChild(del);
      item.addEventListener("click", () => selectChat(c.id));
      els.chatList.appendChild(item);
    });
  };

  const renderMessages = () => {
    const c = activeChat();
    els.messages.innerHTML = "";
    if (!c || c.messages.length === 0) {
      els.welcome.classList.remove("hidden");
      return;
    }
    els.welcome.classList.add("hidden");
    c.messages.forEach((m) => {
      const text = m.role === "user" ? displayUserText(m) : getTextFromContent(m.content);
      const images = getImagesFromContent(m.content);
      const files = m.meta?.files || [];
      const warnings = m.meta?.warnings || [];
      const wrap = appendMessageDom(m.role, text, images, files, warnings);
      if (m.role === "assistant" && m.meta?.artifacts?.length) {
        renderArtifacts(wrap.querySelector(".content"), m.meta.artifacts);
      }
    });
    scrollToBottom(true);
  };

  const summarizeWarnings = (warnings) => {
    if (!warnings?.length) return [];
    const unique = [...new Set(warnings)];
    return unique.slice(0, 5);
  };

  const appendMessageDom = (role, content, images = [], fileNames = [], warnings = []) => {
    const wrap = document.createElement("div");
    wrap.className = "msg " + role;

    const inner = document.createElement("div");
    inner.className = "inner";

    if (role === "assistant") {
      const av = document.createElement("div");
      av.className = "avatar bot";
      av.textContent = "⚡";
      inner.appendChild(av);
      const contentEl = document.createElement("div");
      contentEl.className = "content";
      contentEl.innerHTML = renderMarkdown(content);
      enhanceCodeBlocks(contentEl);
      inner.appendChild(contentEl);
    } else {
      // User message
      const msgContent = document.createElement("div");
      msgContent.className = "user-msg-content";
      
      if (fileNames.length > 0) {
        const chips = document.createElement("div");
        chips.className = "msg-file-chips";
        fileNames.forEach((name) => {
          const chip = document.createElement("span");
          chip.className = "msg-file-chip";
          chip.textContent = name;
          chip.title = name;
          chips.appendChild(chip);
        });
        msgContent.appendChild(chips);
      }

      const warnLines = summarizeWarnings(warnings);
      if (warnLines.length > 0) {
        const warnEl = document.createElement("div");
        warnEl.className = "msg-ingest-warnings";
        warnEl.title = warnings.join("\n");
        warnLines.forEach((line) => {
          const p = document.createElement("p");
          p.textContent = line;
          warnEl.appendChild(p);
        });
        if (warnings.length > warnLines.length) {
          const more = document.createElement("p");
          more.className = "more";
          more.textContent = `+${warnings.length - warnLines.length} more ingest notes`;
          warnEl.appendChild(more);
        }
        msgContent.appendChild(warnEl);
      }

      if (images.length > 0) {
        const imagesDiv = document.createElement("div");
        imagesDiv.className = "msg-images";
        images.forEach((src) => {
          const img = document.createElement("img");
          img.src = src;
          img.alt = "Uploaded image";
          img.addEventListener("click", () => openLightbox(src));
          imagesDiv.appendChild(img);
        });
        msgContent.appendChild(imagesDiv);
      }
      
      const bubble = document.createElement("div");
      bubble.className = "bubble";
      bubble.textContent = content;
      msgContent.appendChild(bubble);
      
      inner.appendChild(msgContent);
      
      const av = document.createElement("div");
      av.className = "avatar user";
      av.textContent = "You";
      av.style.fontSize = "11px";
      inner.appendChild(av);
    }

    wrap.appendChild(inner);
    els.messages.appendChild(wrap);
    return wrap;
  };

  const scrollToBottom = (force = false) => {
    const el = els.chat;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (force || nearBottom) {
      el.scrollTop = el.scrollHeight;
    }
  };

  // ---------- Lightbox ----------
  let lightboxEl = null;
  
  const openLightbox = (src) => {
    if (!lightboxEl) {
      lightboxEl = document.createElement("div");
      lightboxEl.className = "lightbox hidden";
      lightboxEl.innerHTML = `
        <button class="lightbox-close">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M18 6L6 18M6 6l12 12"/>
          </svg>
        </button>
        <img src="" alt="Full size" />
      `;
      lightboxEl.addEventListener("click", closeLightbox);
      document.body.appendChild(lightboxEl);
    }
    lightboxEl.querySelector("img").src = src;
    lightboxEl.classList.remove("hidden");
    document.body.style.overflow = "hidden";
  };
  
  const closeLightbox = () => {
    if (lightboxEl) {
      lightboxEl.classList.add("hidden");
      document.body.style.overflow = "";
    }
  };

  // ---------- Streaming chat ----------
  const setStreaming = (on) => {
    isStreaming = on;
    els.sendBtn.disabled = on;
    els.stopBtn.classList.toggle("hidden", !on);
    if (on) {
      els.input.setAttribute("disabled", "true");
      els.attachBtn.setAttribute("disabled", "true");
    } else {
      els.input.removeAttribute("disabled");
      els.attachBtn.removeAttribute("disabled");
      els.input.focus();
    }
  };

  const sendChat = async (c, content, attachments, userShown = false) => {
    let docItems = attachments.filter((a) => a.kind === "document");
    let imageItems = attachments.filter((a) => a.kind === "image");
    // Over the per-request image limit: send images through ingest to be transcribed.
    if (imageItems.length > MAX_DIRECT_IMAGES) {
      docItems = docItems.concat(imageItems);
      imageItems = [];
    }
    const displayImages = [];
    const fileNames = docItems.map((d) => d.name).filter(Boolean);

    let ingestPayload = null;
    let statusEl = null;
    setStreaming(true);
    try {
      if (docItems.length > 0) {
        els.welcome.classList.add("hidden");
        statusEl = appendMessageDom("assistant", "");
        const statusContent = statusEl.querySelector(".content");
        statusContent.classList.add("typing");
        statusContent.textContent = `Reading ${docItems.length} attachment(s)…`;
        scrollToBottom(true);
        ingestPayload = await ingestDocuments(docItems);
      }
    } catch (e) {
      statusEl?.remove();
      setStreaming(false);
      alert(e.message || "Could not parse documents");
      return;
    }
    statusEl?.remove();
    setStreaming(false);

    const defaultDocPrompt =
      "Please read the attached document(s) and help with my question.";
    const defaultImagePrompt = "What's in this image?";

    let bodyText = content;
    if (ingestPayload?.combined_text) {
      bodyText = `${content || defaultDocPrompt}\n\n--- Attached documents ---\n${ingestPayload.combined_text}`;
    }

    const hasVision =
      imageItems.length > 0 || (ingestPayload?.images?.length || 0) > 0;

    let messageContent;
    if (hasVision || ingestPayload?.combined_text) {
      messageContent = [];
      imageItems.forEach((img) => {
        messageContent.push({
          type: "image_url",
          image_url: { url: img.dataUrl },
        });
        displayImages.push(img.dataUrl);
      });
      (ingestPayload?.images || []).forEach((img) => {
        const url = `data:${img.mime};base64,${img.data_base64}`;
        messageContent.push({
          type: "image_url",
          image_url: { url },
        });
        displayImages.push(url);
      });
      const textPart =
        bodyText ||
        (imageItems.length ? defaultImagePrompt : defaultDocPrompt);
      messageContent.push({ type: "text", text: textPart });
    } else {
      messageContent = content;
    }

    const userText =
      content ||
      (fileNames.length
        ? `Attached: ${fileNames.join(", ")}`
        : imageItems.length
          ? defaultImagePrompt
          : "");

    const meta = {
      files: fileNames,
      warnings: ingestPayload?.warnings || [],
    };

    c.messages.push({
      role: "user",
      content: messageContent,
      userText,
      meta,
    });
    if (!c.title || c.title === "New chat") {
      c.title = titleFromMessage(userText || content || "Attachments");
    }
    c.updatedAt = Date.now();
    save();
    renderSidebar();

    if (!userShown) {
      els.welcome.classList.add("hidden");
      appendMessageDom("user", userText, displayImages, fileNames, meta.warnings);
      clearPendingAttachments();
    }
    scrollToBottom(true);

    const assistantWrap = appendMessageDom("assistant", "");
    const contentEl = assistantWrap.querySelector(".content");
    contentEl.classList.add("typing");
    scrollToBottom(true);

    setStreaming(true);
    abortController = new AbortController();
    let accumulated = "";

    try {
      const res = await fetch(apiUrl("/api/chat"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          messages: c.messages.map((m) => ({
            role: m.role,
            content: m.content,
          })),
          stream: true,
        }),
        signal: abortController.signal,
      });

      if (!res.ok || !res.body) {
        const msg = "Request failed: " + res.status + " " + res.statusText;
        contentEl.classList.remove("typing");
        contentEl.innerHTML = renderMarkdown("**Error.** " + msg);
        throw new Error(msg);
      }

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const event = buffer.slice(0, idx);
          buffer = buffer.slice(idx + 2);
          const line = event.split("\n").find((l) => l.startsWith("data:"));
          if (!line) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          try {
            const obj = JSON.parse(payload);
            if (obj.delta) {
              accumulated += obj.delta;
              contentEl.innerHTML = renderMarkdown(accumulated);
              scrollToBottom();
            } else if (obj.error) {
              accumulated += "\n\n**Error:** " + obj.error;
              contentEl.innerHTML = renderMarkdown(accumulated);
            } else if (obj.done) {
              // end of stream
            }
          } catch {
            // ignore malformed chunk
          }
        }
      }
    } catch (e) {
      if (e.name === "AbortError") {
        accumulated += accumulated ? "\n\n_(stopped)_" : "_(stopped)_";
        contentEl.innerHTML = renderMarkdown(accumulated);
      } else {
        console.error(e);
      }
    } finally {
      contentEl.classList.remove("typing");
      enhanceCodeBlocks(contentEl);
      abortController = null;
      setStreaming(false);
      if (accumulated.trim()) {
        c.messages.push({ role: "assistant", content: accumulated });
        c.updatedAt = Date.now();
        save();
      } else {
        assistantWrap.remove();
      }
      scrollToBottom(true);
    }
  };

  // ---------- File creation ----------
  const CREATE_VERB =
    /(create|make|generate|produce|build|write|draft|prepare|design|export|turn .* into|convert|stw[oó]rz|zr[oó]b|wygeneruj|przygotuj|napisz|opracuj|zaprojektuj|wyeksportuj|zamie[nń])/i;
  const FILE_NOUN =
    /(pdf|docx|word|document|report|whitepaper|brief|ebook|e-book|presentation|deck|slides?|pptx|powerpoint|image|picture|illustration|logo|poster|banner|file|raport|dokument|prezentacj|slajd|obraz|ilustracj|grafik|plik|zdj[eę]ci)/i;
  const EDIT_VERB =
    /\b(edit|update|revise|rewrite|fix|correct|change|modify|translate|proofread|shorten|extend|add|remove|edytuj|popraw|zmie[nń]|zaktualizuj|przet[lł]umacz|skr[oó][cć]|rozszerz|dodaj|usu[nń])\b/i;
  const REFERS_BACK = /\b(this|above|previous|same|ten|tego|powy[zż]sz\w*|poprzedni\w*)\b/i;
  const EDITABLE_EXT = new Set([".docx", ".pptx", ".xlsx", ".txt", ".md", ".markdown", ".csv", ".json"]);

  const looksLikeFileRequest = (text, hasFiles, hasArtifact) =>
    (CREATE_VERB.test(text) && FILE_NOUN.test(text)) ||
    ((hasFiles || hasArtifact) && EDIT_VERB.test(text));

  const latestArtifact = (c) => {
    for (let i = c.messages.length - 1; i >= 0; i--) {
      const list = c.messages[i].meta?.artifacts;
      if (list?.length) return list[list.length - 1];
    }
    return null;
  };

  const historyText = (c) => {
    const out = [];
    let used = 0;
    for (let i = c.messages.length - 1; i >= 0 && out.length < 6; i--) {
      const m = c.messages[i];
      const t = (m.role === "user" ? displayUserText(m) : getTextFromContent(m.content)).slice(0, 1500);
      if (used + t.length > 6000) break;
      used += t.length;
      out.unshift(`${m.role === "user" ? "User" : "Assistant"}: ${t}`);
    }
    return out.join("\n\n");
  };

  const formatSize = (n) =>
    n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

  const b64ToBlob = (b64, mime) => {
    const bin = atob(b64);
    const bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new Blob([bytes], { type: mime });
  };

  const downloadBlob = (blob, name) => {
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  };

  const KIND_LABEL = { pdf: "PDF", docx: "DOCX", pptx: "PPTX", xlsx: "XLSX", image: "IMG", deck: "PPTX" };

  const el = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  };

  const renderFileCard = (a) => {
    const card = el("div", "artifact");
    const head = el("div", "artifact-head");
    head.appendChild(el("span", `artifact-badge kind-${a.kind}`, KIND_LABEL[a.kind] || (a.kind || "FILE").toUpperCase()));
    const info = el("div", "artifact-info");
    info.appendChild(el("div", "artifact-name", a.name));
    info.appendChild(el("div", "artifact-meta", formatSize(a.size || 0)));
    head.appendChild(info);
    const actions = el("div", "artifact-actions");
    const withBlob = async (fn) => {
      const rec = await fileStore.get(a.id);
      if (!rec?.blob) {
        alert("This file is no longer stored in this browser. Ask again to regenerate it.");
        return;
      }
      fn(rec.blob);
    };
    if (a.mime === "application/pdf" || a.kind === "image") {
      const open = el("button", "artifact-btn ghost", "Open");
      open.type = "button";
      open.addEventListener("click", () =>
        withBlob((blob) => window.open(URL.createObjectURL(blob), "_blank", "noopener"))
      );
      actions.appendChild(open);
    }
    const dl = el("button", "artifact-btn", "Download");
    dl.type = "button";
    dl.addEventListener("click", () => withBlob((blob) => downloadBlob(blob, a.name)));
    actions.appendChild(dl);
    head.appendChild(actions);
    card.appendChild(head);
    if (a.kind === "image") {
      const img = el("img", "artifact-image");
      img.alt = a.name;
      fileStore.get(a.id).then((rec) => {
        if (rec?.blob) {
          img.src = URL.createObjectURL(rec.blob);
          img.addEventListener("click", () => openLightbox(img.src));
        } else img.remove();
      });
      card.appendChild(img);
    }
    return card;
  };

  const renderDeckCard = (a) => {
    const card = el("div", "artifact deck");
    const head = el("div", "artifact-head");
    head.appendChild(el("span", "artifact-badge kind-deck", "PPTX"));
    const info = el("div", "artifact-info");
    info.appendChild(el("div", "artifact-name", a.title || "Presentation"));
    const metaLine = el("div", "artifact-meta", "Queued on the deck engine…");
    info.appendChild(metaLine);
    head.appendChild(info);
    const actions = el("div", "artifact-actions");
    head.appendChild(actions);
    card.appendChild(head);
    const bar = el("div", "artifact-progress");
    const fill = el("div", "fill");
    bar.appendChild(fill);
    card.appendChild(bar);
    const grid = el("div", "deck-previews");
    card.appendChild(grid);

    const poll = async () => {
      if (!document.body.contains(card) && card.dataset.mounted) return;
      card.dataset.mounted = "1";
      let st;
      try {
        const res = await fetch(apiUrl(`/api/deck/${encodeURIComponent(a.job_id)}`));
        if (!res.ok) throw new Error((await res.json().catch(() => ({}))).detail || res.statusText);
        st = await res.json();
      } catch (e) {
        metaLine.textContent = `Deck status unavailable: ${e.message}`;
        bar.remove();
        return;
      }
      if (st.status === "done") {
        bar.remove();
        metaLine.textContent = `${st.previews?.length || a.slide_count || ""} slides · Admind template`;
        actions.innerHTML = "";
        const dl = el("a", "artifact-btn", "Download");
        dl.href = st.download_url;
        dl.rel = "noopener";
        actions.appendChild(dl);
        grid.innerHTML = "";
        (st.previews || []).forEach((src, i) => {
          const img = el("img");
          img.src = src;
          img.alt = `Slide ${i + 1}`;
          img.loading = "lazy";
          img.addEventListener("click", () => openLightbox(src));
          grid.appendChild(img);
        });
        (st.warnings || []).slice(0, 3).forEach((w) => card.appendChild(el("p", "artifact-warning", w)));
        return;
      }
      if (st.status === "error") {
        bar.remove();
        metaLine.textContent = st.message || "The deck engine failed";
        return;
      }
      metaLine.textContent = st.message || "Working…";
      fill.style.width = `${Math.max(4, st.progress || 0)}%`;
      setTimeout(poll, 4000);
    };
    setTimeout(poll, 50);
    return card;
  };

  const renderArtifacts = (container, artifacts) => {
    const list = el("div", "artifacts");
    artifacts.forEach((a) => list.appendChild(a.kind === "deck" ? renderDeckCard(a) : renderFileCard(a)));
    container.appendChild(list);
  };

  const ACTION_LABEL = {
    pdf_report: "PDF report",
    docx_document: "Word document",
    presentation: "presentation",
    edit_file: "edit",
    image: "image",
  };

  // Returns false when the server decides the message is ordinary chat.
  const runCreate = async (c, content, attachments, mode) => {
    const fileNames = attachments.map((a) => a.name).filter(Boolean);
    const images = attachments.filter((a) => a.kind === "image").map((a) => a.dataUrl);
    els.welcome.classList.add("hidden");
    appendMessageDom("user", content, images, fileNames, []);
    clearPendingAttachments();

    const assistantWrap = appendMessageDom("assistant", "");
    const contentEl = assistantWrap.querySelector(".content");
    const status = el("div", "create-status");
    const statusText = el("div", "create-status-text", "Starting…");
    const bar = el("div", "artifact-progress");
    const fill = el("div", "fill");
    bar.appendChild(fill);
    status.appendChild(statusText);
    status.appendChild(bar);
    contentEl.appendChild(status);
    scrollToBottom(true);

    const fd = new FormData();
    fd.append("prompt", content);
    fd.append("action", mode);
    fd.append("history", historyText(c));
    attachments.forEach((a) => fd.append("files", a.file, a.name || a.file.name));
    const prev = latestArtifact(c);
    const refersBack = mode === "edit_file" || EDIT_VERB.test(content) || REFERS_BACK.test(content);
    if (!attachments.length && prev && prev.kind !== "deck" && refersBack) {
      const rec = await fileStore.get(prev.id);
      if (rec?.markdown) {
        fd.append("previous_markdown", rec.markdown);
        fd.append("previous_kind", prev.kind);
      } else if (rec?.blob && EDITABLE_EXT.has(extOf(prev.name))) {
        fd.append("files", new File([rec.blob], prev.name, { type: prev.mime }));
      }
    }

    setStreaming(true);
    abortController = new AbortController();
    const artifacts = [];
    const warnings = [];
    const summaries = [];
    let errorText = "";
    let fallback = false;

    const handle = async (ev) => {
      if (ev.type === "chat") fallback = true;
      else if (ev.type === "progress") {
        statusText.textContent = ev.message;
        fill.style.width = `${ev.pct}%`;
        scrollToBottom();
      } else if (ev.type === "warning") warnings.push(ev.message);
      else if (ev.type === "error") errorText = ev.message;
      else if (ev.type === "file") {
        const id = uid();
        await fileStore.put(id, {
          blob: b64ToBlob(ev.b64, ev.mime),
          name: ev.name,
          mime: ev.mime,
          markdown: ev.markdown || "",
        });
        artifacts.push({ id, kind: ev.kind, name: ev.name, mime: ev.mime, size: ev.size });
        summaries.push(ev.summary);
      } else if (ev.type === "deck_job") {
        artifacts.push({ kind: "deck", job_id: ev.job_id, title: ev.title, slide_count: ev.slide_count });
        summaries.push(`Building a ${ev.slide_count}-slide Admind presentation: **${ev.title}**. Previews appear below when it is ready.`);
      }
    };

    try {
      const res = await fetch(apiUrl("/api/create"), {
        method: "POST",
        body: fd,
        signal: abortController.signal,
      });
      if (!res.ok || !res.body) {
        let detail = `${res.status} ${res.statusText}`;
        try {
          detail = (await res.json()).detail || detail;
        } catch {
          /* ignore */
        }
        throw new Error(detail);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let idx;
        while ((idx = buffer.indexOf("\n\n")) !== -1) {
          const line = buffer.slice(0, idx).split("\n").find((l) => l.startsWith("data:"));
          buffer = buffer.slice(idx + 2);
          if (!line) continue;
          try {
            await handle(JSON.parse(line.slice(5).trim()));
          } catch (e) {
            console.warn("bad create event", e);
          }
        }
      }
    } catch (e) {
      errorText = e.name === "AbortError" ? "Stopped." : e.message || String(e);
    } finally {
      abortController = null;
      setStreaming(false);
    }

    if (fallback) {
      assistantWrap.remove();
      await sendChat(c, content, attachments, true);
      return;
    }

    status.remove();
    const label = ACTION_LABEL[mode] || "file";
    let text = summaries.join("\n\n");
    if (!text) text = `**Could not create the ${label}.** ${errorText || "No file was returned."}`;
    else if (errorText) text += `\n\n**Note:** ${errorText}`;
    contentEl.innerHTML = renderMarkdown(text);
    if (artifacts.length) renderArtifacts(contentEl, artifacts);
    warnings.slice(0, 5).forEach((w) => contentEl.appendChild(el("p", "artifact-warning", w)));

    c.messages.push({
      role: "user",
      content: fileNames.length ? `${content}\n\n[Attached: ${fileNames.join(", ")}]` : content,
      userText: content,
      meta: { files: fileNames, warnings: [] },
    });
    c.messages.push({ role: "assistant", content: text, meta: { artifacts, warnings } });
    if (!c.title || c.title === "New chat") c.title = titleFromMessage(content);
    c.updatedAt = Date.now();
    save();
    renderSidebar();
    scrollToBottom(true);
  };

  const sendMessage = async (text) => {
    const content = (text || "").trim();
    if ((!content && pendingAttachments.length === 0) || isStreaming) return;
    let c = activeChat();
    if (!c) c = createChat();
    const attachments = pendingAttachments.slice();
    const wantsCreate =
      content &&
      outputMode !== "chat" &&
      (outputMode !== "auto" || looksLikeFileRequest(content, attachments.length > 0, !!latestArtifact(c)));
    if (wantsCreate) return runCreate(c, content, attachments, outputMode);
    return sendChat(c, content, attachments);
  };

  const setOutputMode = (mode) => {
    outputMode = mode;
    els.outputBar?.querySelectorAll(".output-chip").forEach((b) =>
      b.classList.toggle("active", b.dataset.mode === mode)
    );
    const placeholders = {
      pdf_report: "Describe the PDF report (topic, length, charts, images)…",
      docx_document: "Describe the Word document…",
      presentation: "Describe the presentation (topic, audience, number of slides)…",
      edit_file: "Attach a file (or use the last generated one) and say what to change…",
      image: "Describe the image…",
    };
    els.input.placeholder = placeholders[mode] || "Message… (attach files or paste images)";
  };

  els.outputBar?.addEventListener("click", (e) => {
    const chip = e.target.closest(".output-chip");
    if (chip && !isStreaming) setOutputMode(chip.dataset.mode);
  });

  const stopGeneration = () => {
    if (abortController) abortController.abort();
  };

  // ---------- Composer ----------
  const autoResize = () => {
    const ta = els.input;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 220) + "px";
  };

  els.input.addEventListener("input", autoResize);
  els.input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if (isStreaming) return;
      const text = els.input.value;
      els.input.value = "";
      autoResize();
      sendMessage(text);
    }
  });

  els.composer.addEventListener("submit", (e) => {
    e.preventDefault();
    if (isStreaming) return;
    const text = els.input.value;
    els.input.value = "";
    autoResize();
    sendMessage(text);
  });

  els.stopBtn.addEventListener("click", stopGeneration);
  els.newChatBtn.addEventListener("click", () => {
    if (isStreaming) stopGeneration();
    clearPendingAttachments();
    createChat();
    els.input.focus();
  });
  els.clearBtn.addEventListener("click", () => {
    if (confirm("Clear this conversation?")) {
      if (isStreaming) stopGeneration();
      clearActive();
    }
  });

  // ---------- Image upload handlers ----------
  els.attachBtn.addEventListener("click", () => {
    els.fileInput.click();
  });

  els.fileInput.addEventListener("change", async (e) => {
    const files = e.target.files;
    if (!files) return;
    for (const file of files) {
      if (isImageFile(file) || isDocumentFile(file)) {
        await addPendingFile(file);
      }
    }
    els.fileInput.value = "";
  });

  // Paste handler
  document.addEventListener("paste", async (e) => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
      if (item.type.startsWith("image/")) {
        e.preventDefault();
        const file = item.getAsFile();
        if (file) await addPendingFile(file);
      }
    }
  });

  // Drag and drop
  let dragCounter = 0;
  
  document.addEventListener("dragenter", (e) => {
    e.preventDefault();
    dragCounter++;
    if (e.dataTransfer?.types.includes("Files")) {
      els.dragOverlay.classList.remove("hidden");
    }
  });

  document.addEventListener("dragleave", (e) => {
    e.preventDefault();
    dragCounter--;
    if (dragCounter === 0) {
      els.dragOverlay.classList.add("hidden");
    }
  });

  document.addEventListener("dragover", (e) => {
    e.preventDefault();
  });

  document.addEventListener("drop", async (e) => {
    e.preventDefault();
    dragCounter = 0;
    els.dragOverlay.classList.add("hidden");
    
    const files = e.dataTransfer?.files;
    if (!files) return;
    for (const file of files) {
      if (isImageFile(file) || isDocumentFile(file)) {
        await addPendingFile(file);
      }
    }
  });

  // Sidebar toggles
  const isMobile = () => window.innerWidth <= 767;
  
  const closeSidebar = () => {
    if (isMobile()) {
      els.app.classList.remove("sidebar-open");
    } else {
      els.app.classList.add("sidebar-collapsed");
    }
  };
  
  const openSidebar = () => {
    if (isMobile()) {
      els.app.classList.add("sidebar-open");
    } else {
      els.app.classList.remove("sidebar-collapsed");
    }
  };
  
  els.collapseBtn.addEventListener("click", closeSidebar);
  els.openSidebarBtn.addEventListener("click", openSidebar);
  
  // Close sidebar when clicking outside (mobile overlay)
  document.addEventListener("click", (e) => {
    if (!isMobile() || !els.app.classList.contains("sidebar-open")) return;
    const clickedInSidebar = els.sidebar.contains(e.target);
    const clickedMenuBtn = els.openSidebarBtn.contains(e.target);
    if (!clickedInSidebar && !clickedMenuBtn) {
      closeSidebar();
    }
  });
  
  // Close sidebar after selecting a chat on mobile
  els.chatList.addEventListener("click", (e) => {
    if (isMobile() && e.target.closest(".chat-item")) {
      setTimeout(closeSidebar, 150);
    }
  });

  // Suggestion buttons
  document.querySelectorAll(".suggestion").forEach((btn) => {
    btn.addEventListener("click", () => {
      const action = btn.dataset.action;
      if (action === "upload") {
        els.fileInput.click();
        return;
      }
      const p = btn.dataset.prompt || btn.textContent.trim();
      els.input.value = p;
      autoResize();
      els.input.focus();
    });
  });

  // Escape to close lightbox
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      closeLightbox();
    }
  });

  // ---------- Health / model ----------
  const refreshIngestFormats = async () => {
    try {
      const res = await fetch(apiUrl("/api/ingest/formats"));
      const data = await res.json();
      if (Array.isArray(data.allowed_extensions)) {
        DOC_EXT = new Set(data.allowed_extensions.map((e) => e.toLowerCase()));
      }
      if (Number.isFinite(data.vllm_max_images_per_prompt)) {
        MAX_DIRECT_IMAGES = data.vllm_max_images_per_prompt;
      }
    } catch {
      /* keep defaults */
    }
  };

  const refreshCreateCapabilities = async () => {
    try {
      const res = await fetch(apiUrl("/api/create/capabilities"));
      if (!res.ok) return;
      const caps = await res.json();
      const hide = { presentation: !caps.presentation, image: !caps.images };
      els.outputBar?.querySelectorAll(".output-chip").forEach((b) => {
        b.classList.toggle("hidden", !!hide[b.dataset.mode]);
      });
    } catch {
      /* keep all modes visible */
    }
  };

  const refreshHealth = async () => {
    try {
      const res = await fetch(apiUrl("/api/health"));
      const data = await res.json();
      els.modelName.textContent = data.model || "local-model";
      els.modelPill.classList.remove("offline");
      els.modelPill.title = `${data.model}\n${data.endpoint || ""}`;
      
      // Show vision badge if supported
      if (data.vision || data.documents) {
        els.visionBadge?.classList.remove("hidden");
      } else {
        els.visionBadge?.classList.add("hidden");
      }
    } catch {
      els.modelName.textContent = "offline";
      els.modelPill.classList.add("offline");
      els.visionBadge?.classList.add("hidden");
    }
  };

  // ---------- Init ----------
  const init = () => {
    load();
    if (chats.length === 0) createChat();
    if (!getChat(activeId)) activeId = chats[0].id;
    renderSidebar();
    renderMessages();
    autoResize();
    refreshIngestFormats();
    refreshCreateCapabilities();
    refreshHealth();
    setInterval(refreshHealth, 20000);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
